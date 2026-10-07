"""Tests for ingestion.extraction — HTML text extraction."""

from ingestion.extraction import extract_text


class TestExtractHtml:
    def test_inline_and_nested_text_is_extracted_once(self):
        html = (
            "<p>Bonjour le monde</p>"
            "<p>Un <strong>mot</strong> important</p>"
            "<div><p><span>Imbriqué</span></p></div>"
        )

        result = extract_text(html, "text/html")

        assert result.text == "Bonjour le monde Un mot important Imbriqué"
        assert result.word_count == 7

    def test_confluence_macro_body_is_extracted_once(self):
        html = (
            '<ac:structured-macro ac:name="info"><ac:rich-text-body>'
            "<p>Note importante</p>"
            "</ac:rich-text-body></ac:structured-macro>"
        )

        result = extract_text(html, "text/html")

        assert result.text == "Note importante"

    def test_formatted_heading_text_not_repeated_in_body(self):
        html = "<h2><strong>Section</strong></h2><p>Corps</p>"

        result = extract_text(html, "text/html")

        assert result.text == "## Section\nCorps"
        assert result.headings == [{"level": 2, "text": "Section", "offset": 0}]

    def test_heading_keeps_spaces_around_inline_tags(self):
        html = "<h2>Mise <em>en</em> œuvre</h2><p>Corps</p>"

        result = extract_text(html, "text/html")

        assert result.headings[0]["text"] == "Mise en œuvre"
        assert result.text.startswith("## Mise en œuvre\n")

    def test_comments_are_not_extracted(self):
        html = "<p>Visible</p><!-- brouillon interne -->"

        result = extract_text(html, "text/html")

        assert result.text == "Visible"
