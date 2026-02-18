"""Tool/action execution — ported from agent.py:372-438."""

import datetime


class ActionRouter:
    VALID_TOOLS = {"get_time", "search_web", "capture_image"}

    ALIASES = {
        "google": "search_web",
        "browser": "search_web",
        "news": "search_web",
        "search_news": "search_web",
        "look": "capture_image",
        "see": "capture_image",
        "check_time": "get_time",
    }

    def execute(self, action_data):
        raw_action = action_data.get("action", "").lower().strip()
        value = action_data.get("value") or action_data.get("query")
        action = self.ALIASES.get(raw_action, raw_action)

        print(f"ACTION: {raw_action} -> {action}", flush=True)

        if action not in self.VALID_TOOLS:
            if value and isinstance(value, str) and len(value.split()) > 1:
                return f"CHAT_FALLBACK::{value}"
            return "INVALID_ACTION"

        if action == "get_time":
            now = datetime.datetime.now().strftime("%I:%M %p")
            return f"The current time is {now}."

        elif action == "search_web":
            return self._search_web(value)

        elif action == "capture_image":
            return "IMAGE_CAPTURE_TRIGGERED"

        return None

    @staticmethod
    def _search_web(query):
        print(f"Searching web for: {query}...", flush=True)
        try:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                from ddgs import DDGS
            with DDGS() as ddgs:
                results = []
                try:
                    results = list(ddgs.news(query, region='us-en', max_results=1))
                except Exception:
                    pass
                if not results:
                    try:
                        results = list(ddgs.text(query, region='us-en', max_results=1))
                    except Exception:
                        pass
                if results:
                    r = results[0]
                    title = r.get('title', 'No Title')
                    body = r.get('body', r.get('snippet', 'No Body'))
                    return f"SEARCH RESULTS for '{query}':\nTitle: {title}\nSnippet: {body[:300]}"
                return "SEARCH_EMPTY"
        except Exception as e:
            print(f"Search Error: {e}", flush=True)
            return "SEARCH_ERROR"
