"""Unit tests for HandshakeExtractor GraphQL helpers and pure functions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from handshake_mcp_server.scraping.extractor import HandshakeExtractor


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.evaluate = AsyncMock()
    return page


@pytest.fixture
def extractor(mock_page):
    return HandshakeExtractor(mock_page)


class TestFetchGraphQL:
    async def test_returns_data_on_success(self, extractor, mock_page):
        mock_page.evaluate.return_value = {"job": {"id": "123", "title": "Engineer"}}
        result = await extractor._fetch_graphql("query { job }", {"id": "123"})
        assert result == {"job": {"id": "123", "title": "Engineer"}}

    async def test_returns_none_when_evaluate_returns_none(self, extractor, mock_page):
        mock_page.evaluate.return_value = None
        result = await extractor._fetch_graphql("query { job }", {"id": "123"})
        assert result is None

    async def test_returns_none_on_exception(self, extractor, mock_page):
        mock_page.evaluate.side_effect = Exception("network error")
        result = await extractor._fetch_graphql("query { job }", {"id": "123"})
        assert result is None

    async def test_omits_none_valued_variables(self, extractor, mock_page):
        mock_page.evaluate.return_value = {}
        await extractor._fetch_graphql("query", {"id": "123", "empty": None})
        call_kwargs = mock_page.evaluate.call_args[0][1]
        assert "empty" not in call_kwargs["variables"]
        assert call_kwargs["variables"]["id"] == "123"


class TestHtmlToText:
    async def test_returns_text_from_mock(self, extractor, mock_page):
        mock_page.evaluate.return_value = "Hello World"
        result = await extractor._html_to_text("<p>Hello <b>World</b></p>")
        assert result == "Hello World"

    async def test_empty_input_returns_empty_string(self, extractor, mock_page):
        result = await extractor._html_to_text("")
        assert result == ""
        mock_page.evaluate.assert_not_called()
