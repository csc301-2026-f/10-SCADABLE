"""Small extension of Crawl4AI's public Markdown hook, imported only by the adapter."""

from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from .markdown_cleaning import prepare_markdown_html


class ContentMarkdownGenerator(DefaultMarkdownGenerator):
    """Delegate conversion/filtering to the SDK after cleaning an HTML copy."""

    def __init__(
        self,
        *,
        remove_navigation_and_footer=True,
        remove_boilerplate=True,
        excluded_selectors=(),
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.remove_navigation_and_footer = remove_navigation_and_footer
        self.remove_boilerplate = remove_boilerplate
        self.excluded_selectors = tuple(excluded_selectors)

    def generate_markdown(self, input_html: str, *args, **kwargs):
        prepared = prepare_markdown_html(
            input_html,
            remove_navigation_and_footer=self.remove_navigation_and_footer,
            remove_boilerplate=self.remove_boilerplate,
            excluded_selectors=self.excluded_selectors,
        )
        return super().generate_markdown(prepared, *args, **kwargs)
