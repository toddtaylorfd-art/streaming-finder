import html
import json
import re
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import streamlit as st

TMDB_API_BASE = "https://api.themoviedb.org/3"
DEFAULT_REGION = "US"


@dataclass
class SearchResult:
    source: str
    title: str
    result_type: str
    year_or_date: str
    rating: str
    provider_or_network: str
    overview: str
    link: str
    sort_key: str = ""


class TMDbClient:
    def __init__(self, bearer_token: str, region: str = DEFAULT_REGION):
        self.bearer_token = bearer_token.strip()
        self.region = region

    def is_configured(self) -> bool:
        return bool(self.bearer_token)

    def _get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict:
        if not self.is_configured():
            raise RuntimeError("TMDb token not configured.")

        query = urllib.parse.urlencode(params or {})
        url = TMDB_API_BASE + path
        if query:
            url += "?" + query

        request = urllib.request.Request(url)
        request.add_header("Authorization", f"Bearer {self.bearer_token}")
        request.add_header("accept", "application/json")

        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def search_titles(self, query: str, limit: int = 20) -> List[SearchResult]:
        text = query.strip()
        if not text:
            return []

        data = self._get_json(
            "/search/multi",
            {
                "query": text,
                "include_adult": "false",
                "language": "en-US",
                "page": "1",
            },
        )

        results: List[SearchResult] = []
        for item in data.get("results", []):
            media_type = item.get("media_type", "")
            if media_type not in ("movie", "tv"):
                continue

            title = item.get("title") or item.get("name") or "Unknown Title"
            release_date = item.get("release_date") or item.get("first_air_date") or ""
            year = release_date[:4] if len(release_date) >= 4 else "-"
            overview = item.get("overview") or "No description available."
            rating = f"{float(item.get('vote_average') or 0.0):.1f}"
            providers = self._fetch_providers(media_type, int(item.get("id", 0)))
            providers_text = ", ".join(providers) if providers else "No provider data"
            details_url = self._make_details_url(media_type, int(item.get("id", 0)))
            type_label = "Movie" if media_type == "movie" else "TV Show"

            results.append(
                SearchResult(
                    source="TMDb",
                    title=title,
                    result_type=type_label,
                    year_or_date=year,
                    rating=rating,
                    provider_or_network=providers_text,
                    overview=overview,
                    link=details_url,
                    sort_key=year,
                )
            )

            if len(results) >= limit:
                break

        return results

    def _fetch_providers(self, media_type: str, tmdb_id: int) -> List[str]:
        try:
            data = self._get_json(f"/{media_type}/{tmdb_id}/watch/providers")
            region_info = data.get("results", {}).get(self.region, {})
            provider_names: List[str] = []
            for section in ("flatrate", "ads", "free"):
                for provider in region_info.get(section, []):
                    name = provider.get("provider_name")
                    if name and name not in provider_names:
                        provider_names.append(name)
            return provider_names
        except Exception:
            return []

    def _make_details_url(self, media_type: str, tmdb_id: int) -> str:
        if media_type == "movie":
            return f"https://www.themoviedb.org/movie/{tmdb_id}"
        return f"https://www.themoviedb.org/tv/{tmdb_id}"


class TVMazeClient:
    def _get_json(self, url: str):
        request = urllib.request.Request(url)
        request.add_header("accept", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def search_upcoming(self, query: str, limit: int = 100) -> List[SearchResult]:
        query_text = query.strip().lower()
        if not query_text:
            return []

        items = self._get_json("https://api.tvmaze.com/schedule/full")
        results: List[SearchResult] = []

        for item in items:
            show = item.get("_embedded", {}).get("show", {})
            show_name = show.get("name", "Unknown Show")
            episode_name = item.get("name", "")
            network = "-"
            if show.get("network"):
                network = show["network"].get("name", "-")
            elif show.get("webChannel"):
                network = show["webChannel"].get("name", "-")

            summary = item.get("summary") or show.get("summary") or "No description available."
            summary = self._strip_html(summary)

            combined_text = " ".join([show_name, episode_name, network, summary]).lower()
            if query_text not in combined_text:
                continue

            airdate = item.get("airdate", "")
            airtime = item.get("airtime", "")
            airstamp = item.get("airstamp", "")
            sort_key = self._build_sort_key(airdate, airtime, airstamp)
            display_date = self._format_display_datetime(airdate, airtime, airstamp)

            title = show_name if not episode_name else f"{show_name} — {episode_name}"
            results.append(
                SearchResult(
                    source="TVMaze",
                    title=title,
                    result_type="Upcoming Episode",
                    year_or_date=display_date,
                    rating="-",
                    provider_or_network=network,
                    overview=summary,
                    link=show.get("url", "https://www.tvmaze.com/"),
                    sort_key=sort_key,
                )
            )

            if len(results) >= limit:
                break

        results.sort(key=lambda item: item.sort_key)
        return results

    def _build_sort_key(self, airdate: str, airtime: str, airstamp: str) -> str:
        if airstamp:
            try:
                pacific_dt = self._to_pacific(airstamp)
                return pacific_dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        if not airdate:
            return "9999-99-99 99:99"
        return f"{airdate} {airtime or '23:59'}"

    def _format_display_datetime(self, airdate: str, airtime: str, airstamp: str) -> str:
        if airstamp:
            try:
                pacific_dt = self._to_pacific(airstamp)
                return self._format_pacific_datetime(pacific_dt)
            except ValueError:
                pass
        if airdate:
            try:
                date_obj = datetime.strptime(airdate, "%Y-%m-%d")
                pretty_date = date_obj.strftime("%B %d, %Y")
                if airtime:
                    try:
                        time_obj = datetime.strptime(airtime, "%H:%M")
                        pretty_time = time_obj.strftime("%I:%M %p").lstrip("0").lower()
                        return f"{pretty_date} at {pretty_time} Pacific"
                    except ValueError:
                        return pretty_date
                return pretty_date
            except ValueError:
                return airdate
        return "-"

    def _to_pacific(self, airstamp: str) -> datetime:
        utc_dt = datetime.fromisoformat(airstamp.replace("Z", "+00:00"))
        pacific_offset = self._pacific_offset_for_utc(utc_dt)
        return utc_dt.astimezone(timezone(pacific_offset))

    def _pacific_offset_for_utc(self, utc_dt: datetime) -> timedelta:
        year = utc_dt.year
        march_second_sunday = self._nth_weekday_of_month(year, 3, 6, 2)
        november_first_sunday = self._nth_weekday_of_month(year, 11, 6, 1)
        dst_start_utc = datetime(year, 3, march_second_sunday.day, 10, 0, tzinfo=timezone.utc)
        dst_end_utc = datetime(year, 11, november_first_sunday.day, 9, 0, tzinfo=timezone.utc)
        if dst_start_utc <= utc_dt < dst_end_utc:
            return timedelta(hours=-7)
        return timedelta(hours=-8)

    def _nth_weekday_of_month(self, year: int, month: int, weekday: int, n: int) -> datetime:
        first_day = datetime(year, month, 1)
        days_until_weekday = (weekday - first_day.weekday()) % 7
        day = 1 + days_until_weekday + (n - 1) * 7
        return datetime(year, month, day)

    def _format_pacific_datetime(self, pacific_dt: datetime) -> str:
        pretty_date = pacific_dt.strftime("%B %d, %Y")
        pretty_time = pacific_dt.strftime("%I:%M %p").lstrip("0").lower()
        return f"{pretty_date} at {pretty_time} Pacific"

    def _strip_html(self, text: str) -> str:
        text = html.unescape(text)
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"</?p>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"</?(b|i|em|strong)>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", text).strip()


def get_tmdb_token() -> str:
    token = ""
    try:
        token = st.secrets.get("TMDB_BEARER_TOKEN", "")
    except Exception:
        token = ""
    if not token:
        token = st.session_state.get("tmdb_token_input", "")
    return token.strip()


def render_result_card(result: SearchResult):
    with st.container(border=True):
        st.subheader(result.title)
        st.write(f"**Type:** {result.result_type}")
        st.write(f"**Year/Date:** {result.year_or_date}")
        st.write(f"**Rating:** {result.rating}")
        st.write(f"**Provider/Network:** {result.provider_or_network}")
        st.write(f"**Source:** {result.source}")
        st.write(result.overview)
        st.link_button("Open source page", result.link)


def main():
    st.set_page_config(page_title="Streaming Finder", page_icon="🎬", layout="wide")
    st.title("🎬 Streaming Finder")
    st.caption(
        "Title Search uses TMDb. Upcoming Episodes uses TVMaze and automatically puts the next scheduled airing first."
    )

    with st.sidebar:
        st.header("Setup")
        st.write(
            "For local use, either put your TMDb token in `.streamlit/secrets.toml` or paste it below for this session."
        )
        st.text_input(
            "TMDb Read Access Token",
            key="tmdb_token_input",
            type="password",
            help="Used only for Title Search. Upcoming Episodes does not need a token.",
        )
        st.info(
            "When you deploy to Streamlit Community Cloud, store the token in Secrets instead of putting it in your code."
        )

    tmdb_client = TMDbClient(get_tmdb_token())
    tvmaze_client = TVMazeClient()

    col1, col2 = st.columns([3, 2])
    with col1:
        query = st.text_input(
            "Search",
            placeholder="Try: Severance, Saturday Night Live, Academy Awards, Top Gun",
        )
    with col2:
        mode = st.selectbox("Mode", ["Title Search", "Upcoming Episodes"])

    search_clicked = st.button("Search", type="primary")

    if search_clicked:
        if not query.strip():
            st.warning("Please type something to search for.")
            return

        try:
            with st.spinner("Searching..."):
                if mode == "Title Search":
                    if not tmdb_client.is_configured():
                        st.error(
                            "TMDb token missing. Add `TMDB_BEARER_TOKEN` in `.streamlit/secrets.toml` or paste it in the sidebar."
                        )
                        return
                    results = tmdb_client.search_titles(query, limit=20)
                else:
                    results = tvmaze_client.search_upcoming(query=query, limit=100)
        except Exception as exc:
            st.error(f"Search failed: {exc}")
            return

        if not results:
            st.info("No results found.")
            return

        st.success(f"Found {len(results)} result(s).")
        for result in results:
            render_result_card(result)

    with st.expander("How to deploy this website"):
        st.markdown(
            """
1. Put this file in a GitHub repository.
2. Add a `requirements.txt` file.
3. In Streamlit Community Cloud, choose the repo and deploy the app.
4. Put your TMDb token in the app's Secrets as `TMDB_BEARER_TOKEN = \"...\"`.
            """
        )


if __name__ == "__main__":
    main()
