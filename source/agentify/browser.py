"""Thin Playwright wrapper. Actions are keyed by AX-tree element id."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from playwright.sync_api import Locator, Page, sync_playwright

from .ax_tree import AXElement, format_tree, snapshot


@dataclass
class Observation:
    url: str
    elements: list[AXElement]
    truncated: bool
    text: str
    page_text: str = ""
    title: str = ""


class Browser:
    def __init__(
        self,
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 800),
        storage_state: Optional[Union[str, Path]] = None,
    ):
        self._headless = headless
        self._viewport = viewport
        # When set and the file exists, the context starts with these cookies +
        # localStorage — i.e. already logged in. Missing file => fresh context.
        self._storage_state = storage_state
        self._pw = None
        self._browser = None
        self._context = None
        self.page: Optional[Page] = None
        self._locator_map: dict[int, Locator] = {}
        self._ax_map: dict[int, AXElement] = {}

    def start(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs: dict = dict(
            viewport={"width": self._viewport[0], "height": self._viewport[1]},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )
        if self._storage_state and Path(self._storage_state).exists():
            context_kwargs["storage_state"] = str(self._storage_state)
        self._context = self._browser.new_context(**context_kwargs)
        # Hide the most obvious headless/automation tells before any page JS
        # runs. Sites like eBay gate on navigator.webdriver and an empty
        # plugins/languages list and serve an error page to bots otherwise.
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
        )
        self.page = self._context.new_page()

    def stop(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def save_storage_state(self, path: Union[str, Path]) -> Path:
        """Persist cookies + localStorage to `path` for later session reuse.

        Returns the path written. Secrets (the typed credentials) are not part
        of storage_state — only the session artifacts the site set after login.
        """
        assert self._context is not None, "browser not started"
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(p))
        return p

    def __enter__(self) -> "Browser":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- navigation -----------------------------------------------------

    def goto(self, url: str, wait_ms: int = 100) -> None:
        assert self.page is not None
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(wait_ms)

    def go_back(self) -> None:
        assert self.page is not None
        self.page.go_back(wait_until="domcontentloaded")

    def current_url(self) -> str:
        assert self.page is not None
        return self.page.url

    # --- perception -----------------------------------------------------

    def observe(self, max_elements: int = 80) -> Observation:
        assert self.page is not None
        # The previous action may have triggered a navigation; retry the JS
        # snapshot a few times so we don't fail mid-flight.
        last_err: Exception | None = None
        for _ in range(4):
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=8000)
                elements, locator_map, truncated, page_text, title = snapshot(
                    self.page, max_elements
                )
                self._locator_map = locator_map
                self._ax_map = {el.id: el for el in elements}
                return Observation(
                    url=self.page.url,
                    elements=elements,
                    truncated=truncated,
                    page_text=page_text,
                    title=title,
                    text=format_tree(
                        elements, self.page.url, truncated, page_text, title
                    ),
                )
            except Exception as e:
                last_err = e
                try:
                    self.page.wait_for_timeout(600)
                except Exception:
                    pass
        raise last_err or RuntimeError("observe failed")

    def _resolve(self, element_id: int) -> Locator:
        if element_id not in self._locator_map:
            raise ValueError(
                f"element id {element_id} is not in the current snapshot — "
                "call observe() again before acting"
            )
        return self._locator_map[element_id]

    # --- actions --------------------------------------------------------

    def click(self, element_id: int) -> str:
        loc = self._resolve(element_id)
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=4000)
        return f"clicked [{element_id}]"

    def type_text(self, element_id: int, text: str, press_enter: bool = False) -> str:
        loc = self._resolve(element_id)
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.fill(text, timeout=4000)
        if press_enter:
            loc.press("Enter")
        return f"typed into [{element_id}]" + (" + Enter" if press_enter else "")

    def select_option(self, element_id: int, value: str) -> str:
        loc = self._resolve(element_id)
        loc.scroll_into_view_if_needed(timeout=2000)
        # Try value first; if that fails, try label.
        try:
            loc.select_option(value=value, timeout=2000)
        except Exception:
            loc.select_option(label=value, timeout=2000)
        return f"selected {value!r} on [{element_id}]"

    def scroll(self, direction: str) -> str:
        assert self.page is not None
        direction = (direction or "down").lower()
        if direction == "down":
            self.page.mouse.wheel(0, 600)
        elif direction == "up":
            self.page.mouse.wheel(0, -600)
        elif direction == "top":
            self.page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            raise ValueError(f"unknown scroll direction: {direction!r}")
        self.page.wait_for_timeout(50)
        return f"scrolled {direction}"

    def press_key(self, key: str) -> str:
        assert self.page is not None
        self.page.keyboard.press(key)
        return f"pressed {key}"

    def wait(self, ms: int) -> str:
        assert self.page is not None
        ms = max(0, min(int(ms), 5000))
        self.page.wait_for_timeout(ms)
        return f"waited {ms}ms"
