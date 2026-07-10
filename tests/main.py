"""Lichtgewicht test-runner. Draai met `python tests/main.py`."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sample_articles() -> list[dict]:
    return [
        {
            'title': 'OpenAI lanceerde nieuwe API',
            'summary': 'OpenAI heeft een nieuwe API gelanceerd. Deze is sneller en goedkoper.',
            'links': ['https://openai.com/blog/foo', 'https://example.com/bar'],
            'sources': ['Bron 1', 'Bron 2'],
        },
        {
            'title': 'Anthropic kondigde Claude 5 aan',
            'summary': 'Anthropic heeft Claude 5 aangekondigd. Het model is beschikbaar vanaf juli.',
            'links': ['https://anthropic.com/claude-5'],
            'sources': ['Bron 3'],
        },
    ]


def _patch_cache_prefix(tmpdir: Path):
    """Patch cache_file_prefix zodat caches in tmpdir landen."""
    return patch('src.ai.cache_file_prefix', lambda schedule: str(tmpdir / f'test_{schedule}'))


def test_cache_hit_skips_llm():
    """Bij --cached + bestaand _edited.jsonl moet de LLM NIET worden aangeroepen."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with _patch_cache_prefix(tmpdir):
            from src.ai import edit_articles

            # Schrijf een cache-file zoals edit_articles dat zou doen
            cached_data = [
                {'title': 'cached', 'summary': 'cached summary', 'links': [], 'sources': []}
            ]
            cache_path = tmpdir / 'test_daily_edited.jsonl'
            with open(cache_path, 'w') as f:
                for art in cached_data:
                    f.write(json.dumps(art) + '\n')

            # Model.__init__ exploderen als de mock toch wordt aangeroepen
            with patch('src.ai.Model', side_effect=AssertionError('Model should not be instantiated on cache hit')):
                result = edit_articles('daily', _sample_articles(), cached=True)

            assert result == cached_data, f'Expected cached data, got {result}'
    print('  PASS test_cache_hit_skips_llm')


def test_preserves_links_and_sources():
    """Editor mag links en sources NIET aanraken."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with _patch_cache_prefix(tmpdir):
            from src.ai import edit_articles, EditedArticle

            # Mock Model: prompt() geeft EditedArticle instance terug met andere title/summary
            mock_instance = MagicMock()
            mock_instance.prompt.side_effect = [
                EditedArticle(title='herschreven titel 1', summary='herschreven samenvatting 1'),
                EditedArticle(title='herschreven titel 2', summary='herschreven samenvatting 2'),
            ]
            with patch('src.ai.Model', return_value=mock_instance):
                original = _sample_articles()
                result = edit_articles('daily', original, cached=False)

            assert len(result) == 2
            # Title en summary zijn herschreven
            assert result[0]['title'] == 'herschreven titel 1'
            assert result[0]['summary'] == 'herschreven samenvatting 1'
            # Links en sources zijn ONGEWIJZIGD
            assert result[0]['links'] == original[0]['links']
            assert result[0]['sources'] == original[0]['sources']
            assert result[1]['links'] == original[1]['links']
            assert result[1]['sources'] == original[1]['sources']
    print('  PASS test_preserves_links_and_sources')


def test_handles_dict_response():
    """justai.Model.prompt kan een dict teruggeven; edit_articles moet dat verwerken."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with _patch_cache_prefix(tmpdir):
            from src.ai import edit_articles

            mock_instance = MagicMock()
            mock_instance.prompt.side_effect = [
                {'title': 'dict titel', 'summary': 'dict samenvatting'},
                {'title': 'dict titel 2', 'summary': 'dict samenvatting 2'},
            ]
            with patch('src.ai.Model', return_value=mock_instance):
                result = edit_articles('daily', _sample_articles(), cached=False)

            assert result[0]['title'] == 'dict titel'
            assert result[0]['summary'] == 'dict samenvatting'
            assert result[1]['title'] == 'dict titel 2'
    print('  PASS test_handles_dict_response')


def test_writes_cache_file():
    """Na een niet-cached run moet er een _edited.jsonl bestaan met juiste velden."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with _patch_cache_prefix(tmpdir):
            from src.ai import edit_articles, EditedArticle

            mock_instance = MagicMock()
            mock_instance.prompt.side_effect = [
                EditedArticle(title='t1', summary='s1'),
                EditedArticle(title='t2', summary='s2'),
            ]
            with patch('src.ai.Model', return_value=mock_instance):
                edit_articles('daily', _sample_articles(), cached=False)

            cache_path = tmpdir / 'test_daily_edited.jsonl'
            assert cache_path.exists(), 'cache-bestand ontbreekt'
            lines = cache_path.read_text().strip().split('\n')
            assert len(lines) == 2
            for line in lines:
                obj = json.loads(line)
                assert 'title' in obj and 'summary' in obj
                assert 'links' in obj and 'sources' in obj
    print('  PASS test_writes_cache_file')


def test_retry_prompt_retries_connection_error():
    """retry_prompt moet ConnectionException opvangen en opnieuw proberen."""
    from justai.models.basemodel import ConnectionException
    from src.ai import retry_prompt

    mock_model = MagicMock()
    mock_model.prompt.side_effect = [
        ConnectionException('reset by peer'),
        ConnectionException('reset by peer'),
        {'image_index': 0, 'infographic_index': 1},
    ]
    with patch('src.ai.time.sleep'):
        result = retry_prompt(mock_model, 'test prompt')

    assert result == {'image_index': 0, 'infographic_index': 1}
    assert mock_model.prompt.call_count == 3
    print('  PASS test_retry_prompt_retries_connection_error')


def test_retry_prompt_exhausts_and_reraises_connection_error():
    """Na 5 mislukkingen moet retry_prompt de laatste ConnectionException re-raisen."""
    from justai.models.basemodel import ConnectionException
    from src.ai import retry_prompt

    mock_model = MagicMock()
    mock_model.prompt.side_effect = [ConnectionException(f'boom {i}') for i in range(5)]
    with patch('src.ai.time.sleep'):
        try:
            retry_prompt(mock_model, 'test prompt')
            raise AssertionError('expected ConnectionException, got no exception')
        except ConnectionException as e:
            assert 'boom 4' in str(e), f'expected last exception (boom 4), got: {e}'

    assert mock_model.prompt.call_count == 5
    print('  PASS test_retry_prompt_exhausts_and_reraises_connection_error')


def test_retry_prompt_still_retries_ratelimit():
    """Regressie: RatelimitException blijft ook opgevangen worden."""
    from justai.models.basemodel import RatelimitException
    from src.ai import retry_prompt

    mock_model = MagicMock()
    mock_model.prompt.side_effect = [
        RatelimitException('rate limit'),
        {'ok': True},
    ]
    with patch('src.ai.time.sleep'):
        result = retry_prompt(mock_model, 'test prompt')

    assert result == {'ok': True}
    assert mock_model.prompt.call_count == 2
    print('  PASS test_retry_prompt_still_retries_ratelimit')


def test_retry_prompt_uses_exponential_backoff():
    """Sleep-waardes moeten oplopen: 5, 10, 20, 40, ... (max 60)."""
    from justai.models.basemodel import ConnectionException
    from src.ai import retry_prompt

    mock_model = MagicMock()
    mock_model.prompt.side_effect = [ConnectionException('boom') for _ in range(5)]
    with patch('src.ai.time.sleep') as mock_sleep:
        try:
            retry_prompt(mock_model, 'test prompt')
        except ConnectionException:
            pass

    # 4 sleeps na 4 mislukkingen (na de 5e failure geen sleep meer, direct re-raise).
    sleeps = [call.args[0] for call in mock_sleep.call_args_list]
    assert sleeps == [5, 10, 20, 40], f'expected [5, 10, 20, 40], got {sleeps}'
    print('  PASS test_retry_prompt_uses_exponential_backoff')


def main():
    os.environ.setdefault('DATABASE_URL', 'postgresql://test/test')
    tests = [
        test_cache_hit_skips_llm,
        test_preserves_links_and_sources,
        test_handles_dict_response,
        test_writes_cache_file,
        test_retry_prompt_retries_connection_error,
        test_retry_prompt_exhausts_and_reraises_connection_error,
        test_retry_prompt_still_retries_ratelimit,
        test_retry_prompt_uses_exponential_backoff,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f'  FAIL {t.__name__}: {e}')
            failed += 1
        except Exception as e:
            print(f'  ERROR {t.__name__}: {type(e).__name__}: {e}')
            failed += 1
    total = len(tests)
    print(f'\n{total - failed}/{total} tests passed')
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
