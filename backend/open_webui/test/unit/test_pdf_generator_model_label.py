from types import SimpleNamespace

from open_webui.utils.pdf_generator import PDFGenerator


def _generator():
    return PDFGenerator(SimpleNamespace(title="test", messages=[]))


def test_pdf_model_label_prefers_visible_model_name():
    generator = _generator()

    assert (
        generator._format_model_label(
            {"model": "d7f188cd.gpt-5.4", "modelName": "gpt-5.4 | 佬友测试"}
        )
        == "gpt-5.4 | 佬友测试"
    )


def test_pdf_model_label_strips_internal_prefix_for_legacy_messages():
    generator = _generator()

    assert generator._format_model_label({"model": "d7f188cd.gpt-5.4"}) == "gpt-5.4"
    assert generator._format_model_label({"model": "gpt-5.4"}) == "gpt-5.4"
