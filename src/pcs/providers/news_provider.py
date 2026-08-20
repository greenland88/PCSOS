from pcs.providers.base import NewsProvider


class EmptyNewsProvider(NewsProvider):
    def get_news(self, symbols: list[str], start_date: str, end_date: str):
        return []
