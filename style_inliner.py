"""
Shared module for extracting CSS from pages and inlining styles into HTML content.
Used by news_scraper and event_scraper to return HTML with inline styles for accurate display.
Gets ALL styles defined by class, id, tag, and other selectors; inlines them for every tag in content_html.
Supports Selenium-based getComputedStyle inlining for pixel-perfect display (preferred when Selenium is available).
"""
import re
import logging
import time
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin

try:
    import cssutils
    cssutils.log.setLevel(logging.CRITICAL)
except ImportError:
    cssutils = None

from bs4 import BeautifulSoup

def _should_skip_property(prop: str, value: str) -> bool:
    """Skip empty values and unnecessary styles (vendor prefixes, non-rendered)."""
    if not prop or not str(prop).strip():
        return True
    if not value or not str(value).strip():
        return True
    p = prop.lower().strip()
    if p.startswith("-webkit-") or p.startswith("-moz-") or p.startswith("-ms-"):
        return True
    if p.startswith("scroll-margin") or p.startswith("scroll-padding") or p.startswith("scroll-snap-"):
        return True
    if p in ("pointer-events", "user-select", "touch-action", "caret-color", "overflow-anchor"):
        return True
    return False


def _strip_css_comments(css: str) -> str:
    """Remove /* ... */ comments from CSS so selectors and values don't contain comment fragments."""
    return re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)


def _normalize_selector(selector: str) -> str:
    """Strip and collapse whitespace; remove any remaining comment fragments."""
    s = _strip_css_comments(selector)
    s = ' '.join(s.split())
    return s.strip()


def _extract_rules_from_cssutils(sheet) -> Dict[str, Dict[str, str]]:
    """Recursively extract style rules from a cssutils sheet (including @media)."""
    css_rules: Dict[str, Dict[str, str]] = {}
    try:
        for rule in sheet:
            # STYLE_RULE = 1, MEDIA_RULE = 6, etc.
            if rule.type == rule.STYLE_RULE:
                selector = (getattr(rule, 'selectorText', None) or '').strip()
                if not selector:
                    continue
                styles = {}
                for prop in rule.style:
                    pname = (getattr(prop, 'name', None) or '').strip()
                    pvalue = (getattr(prop, 'value', None) or '').strip()
                    if not pname or _should_skip_property(pname, pvalue):
                        continue
                    styles[pname] = pvalue
                if styles:
                    for sel in (s.strip() for s in selector.split(',') if s.strip()):
                        key = _normalize_selector(sel)
                        if key:
                            if key in css_rules:
                                css_rules[key].update(styles)
                            else:
                                css_rules[key] = styles.copy()
            elif getattr(rule, 'cssRules', None) is not None:
                # @media or other nested rule: recurse to get all rules
                try:
                    nested = _extract_rules_from_cssutils(rule.cssRules)
                    for sel, st in nested.items():
                        if sel in css_rules:
                            css_rules[sel].update(st)
                        else:
                            css_rules[sel] = st.copy()
                except Exception:
                    pass
    except Exception as e:
        logging.warning("cssutils extract rules: %s", e)
    return css_rules


def parse_css_rules(css_content: str) -> Dict[str, Dict[str, str]]:
    """
    Parse CSS content and return a dictionary mapping selectors to style properties.
    Includes rules inside @media (and other at-rules). Keeps all styles (no filtering).
    """
    css_rules: Dict[str, Dict[str, str]] = {}
    if not css_content or not css_content.strip():
        return css_rules

    # Strip comments so selectors like .conference-title /* x */ parse as .conference-title
    css_content = _strip_css_comments(css_content)

    # Extract inner content of @media / @supports so we get all rules
    def extract_media_inner(text: str) -> str:
        out = []
        i = 0
        while i < len(text):
            if i < len(text) and text[i] == '@':
                rest = text[i:]
                m = re.match(r'@\s*(?:media|supports)\s*[^{]*\{', rest, re.DOTALL | re.IGNORECASE)
                if m:
                    start_brace = i + m.end() - 1
                    depth = 1
                    j = start_brace + 1
                    while j < len(text) and depth:
                        if text[j] == '{':
                            depth += 1
                        elif text[j] == '}':
                            depth -= 1
                        j += 1
                    inner = text[start_brace + 1:j - 1]
                    out.append(extract_media_inner(inner))
                    i = j
                    continue
                m2 = re.match(r'@[^{]+\{', rest)
                if m2:
                    start_brace = i + rest.index('{')
                    depth = 1
                    j = start_brace + 1
                    while j < len(text) and depth:
                        if text[j] == '{':
                            depth += 1
                        elif text[j] == '}':
                            depth -= 1
                        j += 1
                    i = j
                    continue
            if i < len(text):
                out.append(text[i])
                i += 1
            else:
                break
        return ''.join(out)

    content_to_parse = css_content
    if cssutils is not None:
        try:
            sheet = cssutils.parseString(css_content)
            return _extract_rules_from_cssutils(sheet)
        except Exception as e:
            logging.warning("cssutils parse failed: %s", e)
        # Try with @media inner content so we get inner rules
        try:
            content_to_parse = extract_media_inner(css_content)
            sheet = cssutils.parseString(content_to_parse)
            return _extract_rules_from_cssutils(sheet)
        except Exception:
            pass

    # Regex fallback: parse rules, and also parse inside @media by extracting inner content first
    inner_content = extract_media_inner(css_content)
    for block in (css_content, inner_content):
        pattern = r'([^{]+)\{([^}]+)\}'
        for match in re.finditer(pattern, block, re.DOTALL):
            selector = match.group(1).strip()
            props_block = match.group(2).strip()
            if not selector or selector.startswith('@'):
                continue
            styles = {}
            for prop_match in re.finditer(r'([^:;]+):([^;]+);?', props_block):
                pname = prop_match.group(1).strip()
                pvalue = prop_match.group(2).strip()
                if _should_skip_property(pname, pvalue):
                    continue
                styles[pname] = pvalue
            if styles:
                for sel in (s.strip() for s in selector.split(',') if s.strip()):
                    key = _normalize_selector(sel)
                    if key:
                        if key in css_rules:
                            css_rules[key].update(styles)
                        else:
                            css_rules[key] = styles.copy()

    return css_rules


async def extract_and_parse_css(soup: BeautifulSoup, base_url: Optional[str]) -> Dict[str, Dict[str, str]]:
    """
    Extract all CSS from the document: <link rel="stylesheet"> and <style> tags.
    Returns merged selector -> { property: value }.
    """
    merged_rules: Dict[str, Dict[str, str]] = {}

    for style_tag in soup.find_all('style'):
        if style_tag.string:
            rules = parse_css_rules(style_tag.string)
            for sel, styles in rules.items():
                if sel in merged_rules:
                    merged_rules[sel].update(styles)
                else:
                    merged_rules[sel] = styles.copy()

    try:
        import aiohttp
    except ImportError:
        return merged_rules

    for link in soup.find_all('link', rel='stylesheet'):
        href = link.get('href')
        if not href:
            continue
        url = urljoin(base_url or '', href)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    css_text = await resp.text()
                    rules = parse_css_rules(css_text)
                    for sel, styles in rules.items():
                        if sel in merged_rules:
                            merged_rules[sel].update(styles)
                        else:
                            merged_rules[sel] = styles.copy()
        except Exception as e:
            logging.warning("Failed to fetch CSS %s: %s", url, e)

    return merged_rules


def _strip_pseudo(selector: str) -> str:
    """Strip pseudo-classes and pseudo-elements for matching (e.g. a:hover -> a, li:first-child -> li)."""
    # Remove ::before, ::after, :hover, :first-child, :last-child, :nth-child(...), etc.
    sel = re.sub(r'::[a-zA-Z-]+', '', selector)
    sel = re.sub(r':[a-zA-Z-]+(?:\([^)]*\))?', '', sel)
    return sel.strip()


def _get_simple_selector_part(selector: str) -> str:
    """Get the rightmost part of a selector for matching this element (handles space, >, +, ~)."""
    selector = _strip_pseudo(selector)
    # Split on combinators (space, >, +, ~) and take the last part
    parts = re.split(r'\s+|\s*>\s*|\s*\+\s*|\s*~\s*', selector)
    return parts[-1].strip() if parts else selector


def element_matches_selector(tag: Any, selector: str) -> bool:
    """
    Check if a BeautifulSoup element matches a CSS selector.
    Supports: tag, .class, #id, tag.class, tag#id, .a.b, *, [attr], [attr=value], [attr*=value], etc.
    Pseudo-classes and pseudo-elements are stripped for matching.
    Selectors are normalized (comments stripped, whitespace collapsed) so .conference-title matches.
    """
    selector = _normalize_selector(selector)
    if not selector:
        return False

    simple = _get_simple_selector_part(selector)
    simple = _normalize_selector(simple)
    if not simple:
        return False

    tag_name = (tag.name or '').lower()
    classes = tag.get('class') or []
    if isinstance(classes, str):
        classes = [classes]
    classes = [c for c in classes if c]
    elem_id = (tag.get('id') or '').strip()

    # Universal selector
    if simple == '*':
        return True

    # Attribute selectors: [attr], [attr=value], [attr~=value], [attr|=value], [attr*=value], [attr^=value], [attr$=value]
    attr_match = re.match(r'\[([a-zA-Z_-][a-zA-Z0-9_-]*)(?:([~|^$*]?)=([\'"]?)([^\'"\]]*)\3)?\]', simple)
    if attr_match:
        attr_name = attr_match.group(1).lower()
        op = (attr_match.group(2) or '').strip()
        val = (attr_match.group(4) or '').strip().lower()
        attr_val = (tag.get(attr_name) or '')
        if isinstance(attr_val, list):
            attr_val = ' '.join(str(x) for x in attr_val)
        attr_val = str(attr_val).strip().lower()
        if op == '':
            return bool(attr_val)
        if op == '=':
            return attr_val == val
        if op == '~=':
            return val in attr_val.split()
        if op == '|=':
            return attr_val == val or attr_val.startswith(val + '-')
        if op == '*=':
            return val in attr_val
        if op == '^=':
            return attr_val.startswith(val)
        if op == '$=':
            return attr_val.endswith(val)
        return bool(attr_val)

    # ID selector
    if simple.startswith('#'):
        return elem_id == simple[1:].strip()

    # Class selector(s): .conference-title (one class) or .a.b (multiple classes)
    if simple.startswith('.'):
        parts = [p.strip() for p in simple[1:].split('.') if p.strip()]
        return bool(parts) and all(p in classes for p in parts)

    # Tag with class and/or id (e.g. div.foo, div#id, div#id.foo)
    if '.' in simple or '#' in simple:
        if '#' in simple:
            t_part, rest = simple.split('#', 1)
            # rest can be "id" or "id.foo.bar"
            id_rest = rest.split('.')
            id_val = (id_rest[0] or '').strip()
            class_parts = [p for p in id_rest[1:] if p]
            if '.' in t_part:
                tag_part = t_part.split('.')[0].strip().lower()
                class_parts = [p for p in t_part.split('.')[1:] if p] + class_parts
            else:
                tag_part = t_part.strip().lower()
            if tag_name != tag_part:
                return False
            if elem_id != id_val:
                return False
            return all(p in classes for p in class_parts)
        else:
            parts = simple.split('.')
            tag_part = (parts[0] or '').strip().lower()
            class_parts = [p for p in parts[1:] if p]
            if tag_name != tag_part:
                return False
            return all(p in classes for p in class_parts)

    # Plain tag
    return tag_name == simple.lower()


def inline_css_styles(root: Any, css_rules: Dict[str, Dict[str, str]]) -> None:
    """
    Inline CSS rules into every tag of the HTML tree (BeautifulSoup element).
    Gets all styles defined by class, id, tag, and other selectors; applies them to all matching tags.
    """
    if not css_rules:
        return
    for tag in root.find_all(True):
        if tag.name in ('script', 'style'):
            continue
        existing = tag.get('style') or ''
        inline: Dict[str, str] = {}
        for part in existing.split(';'):
            if ':' in part:
                k, _, v = part.partition(':')
                if k.strip():
                    inline[k.strip()] = v.strip()
        for selector, styles in css_rules.items():
            if not element_matches_selector(tag, selector):
                continue
            for prop, value in styles.items():
                if _should_skip_property(prop, value):
                    continue
                inline[prop] = value
        if inline:
            tag['style'] = '; '.join(f"{k}: {v}" for k, v in sorted(inline.items()))


def get_html_with_computed_styles(
    url: str,
    wait_seconds: int = 2,
    scroll_delay: float = 1.0,
    headless: bool = True,
) -> Optional[str]:
    """
    Same workflow as JobScraper _extract_html_content_with_styles: launch Chrome
    (Selenium), load the URL, wait and scroll, then run getComputedStyle JS and
    return main content HTML with all styles inlined. Chrome/WebDriver will run
    (you may see another port when it runs). Returns None if Selenium is unavailable or fails.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        logging.warning("Selenium or webdriver_manager not installed; cannot fetch styles with Chrome")
        return None

    driver = None
    try:
        print("Loading page with Selenium for computed styles:", url[:80] + ("..." if len(url) > 80 else ""))
        opts = Options()
        if headless:
            opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver.get(url)
        time.sleep(wait_seconds)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_delay)

        # Same as JobScraper: run getComputedStyle inlining script
        html_content = driver.execute_script(_INLINE_COMPUTED_STYLES_JS)
        if html_content:
            print("Selenium computed styles OK:", len(html_content), "chars")
        return html_content if html_content else None
    except Exception as e:
        logging.warning("get_html_with_computed_styles failed for %s: %s", url[:50], e)
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# JavaScript: inline ALL computed styles (getComputedStyle = final resolved values including @media).
# No filtering - every property with a value is inlined so the page matches DevTools "Styles" panel.
_INLINE_COMPUTED_STYLES_JS = r"""
function getAllComputedStyles(el) {
    var s = window.getComputedStyle(el);
    var out = {};
    var propList = [];
    for (var i = 0; i < s.length; i++) propList.push(s[i]);
    var allProps = ['accent-color','align-content','align-items','align-self','animation','animation-delay','animation-direction','animation-duration','animation-fill-mode','animation-iteration-count','animation-name','animation-play-state','animation-timing-function','appearance','aspect-ratio','backdrop-filter','backface-visibility','background','background-attachment','background-blend-mode','background-clip','background-color','background-image','background-origin','background-position','background-position-x','background-position-y','background-repeat','background-size','block-size','border','border-block-end','border-block-start','border-bottom','border-bottom-color','border-bottom-left-radius','border-bottom-right-radius','border-bottom-style','border-bottom-width','border-collapse','border-color','border-image','border-image-outset','border-image-repeat','border-image-slice','border-image-source','border-image-width','border-inline-end','border-inline-start','border-left','border-left-color','border-left-style','border-left-width','border-radius','border-right','border-right-color','border-right-style','border-right-width','border-spacing','border-style','border-top','border-top-color','border-top-left-radius','border-top-right-radius','border-top-style','border-top-width','border-width','bottom','box-decoration-break','box-shadow','box-sizing','break-after','break-before','break-inside','caption-side','caret-color','clear','clip','clip-path','clip-rule','color','color-interpolation','column-count','column-fill','column-gap','column-rule','column-rule-color','column-rule-style','column-rule-width','column-span','column-width','columns','content','cursor','direction','display','empty-cells','fill','fill-opacity','fill-rule','filter','flex','flex-basis','flex-direction','flex-flow','flex-grow','flex-shrink','flex-wrap','float','font','font-family','font-feature-settings','font-kerning','font-size','font-size-adjust','font-stretch','font-style','font-synthesis','font-variant','font-variant-caps','font-variant-east-asian','font-variant-ligatures','font-variant-numeric','font-variant-position','font-weight','gap','grid','grid-area','grid-auto-columns','grid-auto-flow','grid-auto-rows','grid-column','grid-column-end','grid-column-start','grid-row','grid-row-end','grid-row-start','grid-template','grid-template-areas','grid-template-columns','grid-template-rows','height','hyphens','image-orientation','image-rendering','inline-size','inset','inset-block','inset-block-end','inset-block-start','inset-inline','inset-inline-end','inset-inline-start','isolation','justify-content','justify-items','justify-self','left','letter-spacing','line-break','line-height','list-style','list-style-image','list-style-position','list-style-type','margin','margin-block-end','margin-block-start','margin-bottom','margin-inline-end','margin-inline-start','margin-left','margin-right','margin-top','mask','mask-type','max-block-size','max-height','max-inline-size','max-width','min-block-size','min-height','min-inline-size','min-width','mix-blend-mode','object-fit','object-position','offset','offset-anchor','offset-distance','offset-path','offset-rotate','opacity','order','outline','outline-color','outline-offset','outline-style','outline-width','overflow','overflow-anchor','overflow-wrap','overflow-x','overflow-y','overscroll-behavior','overscroll-behavior-x','overscroll-behavior-y','padding','padding-block-end','padding-block-start','padding-bottom','padding-inline-end','padding-inline-start','padding-left','padding-right','padding-top','paint-order','perspective','perspective-origin','pointer-events','position','quotes','resize','right','rotate','row-gap','ruby-position','scale','scroll-behavior','scroll-margin','scroll-margin-block-end','scroll-margin-block-start','scroll-margin-bottom','scroll-margin-inline-end','scroll-margin-inline-start','scroll-margin-left','scroll-margin-right','scroll-margin-top','scroll-padding','scroll-padding-block-end','scroll-padding-block-start','scroll-padding-bottom','scroll-padding-inline-end','scroll-padding-inline-start','scroll-padding-left','scroll-padding-right','scroll-padding-top','scroll-snap-align','scroll-snap-stop','scroll-snap-type','shape-image-threshold','shape-margin','shape-outside','stop-color','stop-opacity','stroke','stroke-dasharray','stroke-dashoffset','stroke-linecap','stroke-linejoin','stroke-miterlimit','stroke-opacity','stroke-width','tab-size','table-layout','text-align','text-align-last','text-anchor','text-combine-upright','text-decoration','text-decoration-color','text-decoration-line','text-decoration-skip-ink','text-decoration-style','text-decoration-thickness','text-indent','text-orientation','text-overflow','text-rendering','text-shadow','text-transform','text-underline-offset','text-underline-position','top','touch-action','transform','transform-box','transform-origin','transform-style','transition','transition-delay','transition-duration','transition-property','transition-timing-function','translate','unicode-bidi','user-select','vertical-align','visibility','white-space','width','will-change','word-break','word-spacing','word-wrap','writing-mode','z-index'];
    for (var j = 0; j < allProps.length; j++) { if (propList.indexOf(allProps[j]) === -1) propList.push(allProps[j]); }
    function shouldSkipProp(name) {
        var n = (name || '').toLowerCase();
        if (n.indexOf('-webkit-') === 0 || n.indexOf('-moz-') === 0 || n.indexOf('-ms-') === 0) return true;
        if (n.indexOf('scroll-margin') === 0 || n.indexOf('scroll-padding') === 0 || n.indexOf('scroll-snap-') === 0) return true;
        if (n === 'pointer-events' || n === 'user-select' || n === 'touch-action' || n === 'caret-color' || n === 'overflow-anchor') return true;
        return false;
    }
    propList.forEach(function(prop) {
        try {
            if (shouldSkipProp(prop)) return;
            var value = s.getPropertyValue(prop);
            if (value !== undefined && value !== null && (value + '').trim() !== '') out[prop] = value;
        } catch (e) {}
    });
    return out;
}
function styleObjToCss(obj) {
    var keys = Object.keys(obj).sort();
    var css = '';
    keys.forEach(function(k) { css += k + ':' + obj[k] + ';'; });
    return css;
}
var styleCache = {};
function inlineAllStyles(el) {
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') return;
    var styles = getAllComputedStyles(el);
    if (Object.keys(styles).length === 0) {
        el.removeAttribute('style');
        Array.from(el.children).forEach(inlineAllStyles);
        return;
    }
    var styleString = styleObjToCss(styles);
    var finalStyle = styleCache[styleString];
    if (!finalStyle) {
        finalStyle = styleString.replace(/;/g, '; ').trim();
        styleCache[styleString] = finalStyle;
    }
    el.setAttribute('style', finalStyle);
    Array.from(el.children).forEach(inlineAllStyles);
}
var headers = document.querySelectorAll('header, footer');
headers.forEach(function(el) { el.remove(); });
function hasHeaderFooter(el) {
    if (el.tagName === 'HEADER' || el.tagName === 'FOOTER') return true;
    var id = (el.getAttribute('id') || '').toLowerCase().trim();
    var cn = (el.getAttribute('class') || '').toLowerCase();
    if (id === 'header' || id === 'footer') return true;
    if (id.indexOf('footer') !== -1 || cn.indexOf('footer') !== -1) return true;
    var cnList = cn.split(/\s+/);
    return cnList.indexOf('header') !== -1;
}
function shouldRemoveElement(el) {
    var id = (el.getAttribute('id') || '').toLowerCase();
    var cn = (el.getAttribute('class') || '').toLowerCase();
    return id.indexOf('search') !== -1 || id.indexOf('apply-button') !== -1 || cn.indexOf('search') !== -1 || cn.indexOf('apply-button') !== -1;
}
var all = document.querySelectorAll('*');
var toRemove = [];
all.forEach(function(el) {
    if (hasHeaderFooter(el) || shouldRemoveElement(el)) toRemove.push(el);
});
toRemove.forEach(function(el) { el.remove(); });
var mainContent = null;
var selectors = ['main', '[role="main"]', '.main-content', '.content', '.job-content', '.job-details', '.job-description', '.article-content', '.article-body', '.news-content', '.event-content', '.event-details', '#main', '#content', '#job-content', '.jobDisplayShell', '.job-posting', '.job-detail'];
for (var i = 0; i < selectors.length; i++) {
    mainContent = document.querySelector(selectors[i]);
    if (mainContent) break;
}
if (!mainContent) mainContent = document.body;
inlineAllStyles(mainContent);
return mainContent.outerHTML;
"""
