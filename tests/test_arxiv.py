"""Tests for ArxivTool — XML parsing, author handling, truncation, and error paths."""

from unittest.mock import MagicMock, patch

from agent.tools.arxiv import ArxivTool

ATOM_NS_URI = "http://www.w3.org/2005/Atom"


def _build_atom_feed(entries: list[dict]) -> bytes:
    """Build a minimal Atom XML feed string from a list of entry dicts."""
    parts = [f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="{ATOM_NS_URI}">']
    for e in entries:
        authors_xml = "".join(
            f"<author><name>{a}</name></author>" for a in e.get("authors", ["Default Author"])
        )
        parts.append(
            "<entry>"
            f"<title>{e.get('title', 'Test Paper')}</title>"
            f"<summary>{e.get('summary', 'Abstract text.')}</summary>"
            f"<id>{e.get('id', 'http://arxiv.org/abs/0000.0000')}</id>"
            f"<published>{e.get('published', '2024-01-15T00:00:00Z')}</published>"
            f"{authors_xml}"
            "</entry>"
        )
    parts.append("</feed>")
    return "".join(parts).encode()


def _mock_response(content: bytes = b"", status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.content = content
    m.raise_for_status = MagicMock()
    return m


class TestArxivToolMeta:
    def test_name(self):
        assert ArxivTool().name == "arxiv_search"

    def test_description_mentions_arxiv(self):
        assert "arXiv" in ArxivTool().description


class TestArxivToolRun:
    def setup_method(self):
        self.tool = ArxivTool()

    def test_successful_single_result(self):
        xml = _build_atom_feed(
            [
                {
                    "title": "ML Credit Risk",
                    "authors": ["Alice Smith", "Bob Jones"],
                    "id": "http://arxiv.org/abs/2401.12345",
                    "published": "2024-03-01T00:00:00Z",
                    "summary": "We study credit risk using ML.",
                }
            ]
        )
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("credit risk machine learning")

        assert result.success is True
        assert "ML Credit Risk" in result.content
        assert len(result.sources) == 1

    def test_source_fields_populated_correctly(self):
        xml = _build_atom_feed(
            [
                {
                    "title": "Test Paper",
                    "authors": ["Jane Doe"],
                    "id": "http://arxiv.org/abs/2401.99999",
                    "published": "2024-06-01T00:00:00Z",
                }
            ]
        )
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        src = result.sources[0]
        assert src["title"] == "Test Paper"
        assert src["authors"] == ["Jane Doe"]
        assert src["url"] == "http://arxiv.org/abs/2401.99999"
        assert src["published"] == "2024-06-01"
        assert src["type"] == "arxiv"

    def test_no_entries_returns_failure(self):
        xml = _build_atom_feed([])
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("xyznonexistent")

        assert result.success is False
        assert "No arXiv papers found" in result.content

    def test_at_most_four_results_returned(self):
        entries = [{"title": f"Paper {i}", "id": f"http://arxiv.org/abs/{i:04d}"} for i in range(5)]
        xml = _build_atom_feed(entries)
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("anything")

        assert result.success is True
        assert len(result.sources) == 4

    def test_more_than_four_authors_get_et_al(self):
        authors = ["A", "B", "C", "D", "E"]  # 5 authors
        xml = _build_atom_feed([{"authors": authors}])
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        assert "et al." in result.content
        assert "E" not in result.content.split("**Authors**:")[1].split("\n")[0]

    def test_exactly_four_authors_no_et_al(self):
        authors = ["A", "B", "C", "D"]
        xml = _build_atom_feed([{"authors": authors}])
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        assert "et al." not in result.content

    def test_newlines_in_title_replaced_with_spaces(self):
        xml = _build_atom_feed([{"title": "Long\nMultiline\nTitle"}])
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        assert result.sources[0]["title"] == "Long Multiline Title"
        assert "\n" not in result.sources[0]["title"]

    def test_abstract_truncated_to_800_chars(self):
        long_abstract = "z" * 2000
        xml = _build_atom_feed([{"summary": long_abstract}])
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        assert result.success is True
        assert "z" * 800 in result.content
        assert "z" * 801 not in result.content

    def test_published_date_truncated_to_yyyy_mm_dd(self):
        xml = _build_atom_feed([{"published": "2023-11-20T18:30:00Z"}])
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        assert result.sources[0]["published"] == "2023-11-20"

    def test_network_error_returns_failure(self):
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.side_effect = ConnectionError("Network unreachable")
            result = self.tool.run("test")

        assert result.success is False
        assert result.error != ""

    def test_multiple_papers_separated_by_divider(self):
        entries = [
            {"title": "Paper A", "id": "http://arxiv.org/abs/0001"},
            {"title": "Paper B", "id": "http://arxiv.org/abs/0002"},
        ]
        xml = _build_atom_feed(entries)
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        assert "---" in result.content
        assert "Paper A" in result.content
        assert "Paper B" in result.content

    def test_content_includes_url_for_each_paper(self):
        xml = _build_atom_feed([{"id": "http://arxiv.org/abs/2401.55555"}])
        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _mock_response(xml)
            result = self.tool.run("test")

        assert "http://arxiv.org/abs/2401.55555" in result.content
