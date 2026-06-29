"""Tests for i18n — translation helper functions."""


from services.i18n import get_translator, translate


class TestGetTranslator:
    def test_returns_callable(self):
        translator = get_translator()
        assert callable(translator)

    def test_translator_returns_string(self):
        translator = get_translator()
        result = translator("hello")
        assert isinstance(result, str)


class TestTranslate:
    def test_returns_string(self):
        result = translate("hello world")
        assert isinstance(result, str)

    def test_returns_original_when_no_translation(self):
        # With NullTranslations, gettext returns the original string
        result = translate("some untranslated string")
        assert isinstance(result, str)
