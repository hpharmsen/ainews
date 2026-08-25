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


# ---------------------------------------------------------------------------
# Kwaliteitspoort: nooit een nieuwsbrief zonder nieuws versturen
# ---------------------------------------------------------------------------

# De letterlijke payload die op 2026-08-25 naar 56 abonnees ging.
INCIDENT_2026_08_25 = [{
    'title': 'Geen bruikbaar nieuws beschikbaar',
    'summary': 'De ontvangen e-mail van AI Central bevat geen concrete, verifieerbare '
               'nieuwsfeiten die geschikt zijn voor de nieuwsbrief. De mail beschrijft een '
               'algemene tip over het analyseren van PDF-rapporten met AI, maar noemt geen '
               'specifieke tools, cijfers, datums of productlanceringen.',
    'links': [],
    'sources': ['AI Central, Kris'],
}]


def _articles(count: int, with_links: int = 99) -> list[dict]:
    """Bouw `count` artikelen, waarvan de eerste `with_links` een bronlink hebben."""
    return [{
        'title': f'Artikel {i}',
        'summary': f'Samenvatting van artikel {i}. Met een tweede zin erbij.',
        'links': [f'https://example.com/{i}'] if i < with_links else [],
        'sources': [f'Bron {i}'],
    } for i in range(count)]


def test_publishable_rejects_incident_2026_08_25():
    """Regressie: de echte payload van 25 augustus moet worden afgekeurd."""
    from src.ai import check_publishable

    reason = check_publishable(INCIDENT_2026_08_25)
    assert reason is not None, 'de lege nieuwsbrief van 25 augustus kwam er doorheen'
    print('  PASS test_publishable_rejects_incident_2026_08_25')


def test_publishable_accepts_normal_newsletter():
    """Een normale set van 4 artikelen met links komt erdoor."""
    from src.ai import check_publishable

    reason = check_publishable(_articles(4))
    assert reason is None, f'normale nieuwsbrief onterecht afgekeurd: {reason}'
    print('  PASS test_publishable_accepts_normal_newsletter')


def test_publishable_rejects_too_few_articles():
    """Twee artikelen is te weinig, ook al hebben ze allebei links."""
    from src.ai import check_publishable

    assert check_publishable(_articles(2)) is not None
    print('  PASS test_publishable_rejects_too_few_articles')


def test_publishable_accepts_newsletter_without_links():
    """Regressie op 18 en 22 augustus: check_and_resolve_url() strijkt zoveel links weg
    dat een echte nieuwsbrief er maar een overhoudt. Die mag niet worden afgekeurd."""
    from src.ai import check_publishable

    reason = check_publishable(_articles(6, with_links=1))
    assert reason is None, f'echte nieuwsbrief zonder links onterecht afgekeurd: {reason}'
    print('  PASS test_publishable_accepts_newsletter_without_links')


def test_publishable_rejects_empty_summary():
    """Een whitespace-only samenvatting wordt afgekeurd."""
    from src.ai import check_publishable

    articles = _articles(4)
    articles[2]['summary'] = '   \n  '
    reason = check_publishable(articles)
    assert reason is not None and 'Artikel 2' in reason, f'onverwachte reden: {reason}'
    print('  PASS test_publishable_rejects_empty_summary')


# ---------------------------------------------------------------------------
# Ophaalvenster: een late vorige run mag het venster niet uithongeren
# ---------------------------------------------------------------------------

def _write_last_sent(tmpdir: Path, schedule: str, moment) -> Path:
    path = tmpdir / 'last_sent.json'
    path.write_text(json.dumps({'last_sent': {schedule: moment.isoformat()}}))
    return path


def test_lookback_floor_when_last_send_was_recent():
    """Kern van het incident: last_sent 2 uur geleden -> venster toch 24 uur terug."""
    from datetime import datetime, timedelta, timezone
    from src import gmail

    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_last_sent(Path(tmp), 'daily', now - timedelta(hours=2))
        with patch.object(gmail, 'LAST_SENT_FILE', path):
            start = gmail.fetch_window_start('daily')

    age = now - start
    assert age >= timedelta(hours=23, minutes=59), f'venster is maar {age}, verwacht >= 24u'
    print('  PASS test_lookback_floor_when_last_send_was_recent')


def test_lookback_uses_last_sent_when_older_than_floor():
    """last_sent 3 dagen geleden -> venster blijft die 3 dagen, niet ingekort tot 24u."""
    from datetime import datetime, timedelta, timezone
    from src import gmail

    now = datetime.now(timezone.utc)
    three_days_ago = now - timedelta(days=3)
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_last_sent(Path(tmp), 'daily', three_days_ago)
        with patch.object(gmail, 'LAST_SENT_FILE', path):
            start = gmail.fetch_window_start('daily')

    assert abs((start - three_days_ago).total_seconds()) < 1, f'verwacht {three_days_ago}, kreeg {start}'
    print('  PASS test_lookback_uses_last_sent_when_older_than_floor')


def test_lookback_weekly_floor_is_a_week():
    """Weekly kijkt minstens 7 dagen terug."""
    from datetime import datetime, timedelta, timezone
    from src import gmail

    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_last_sent(Path(tmp), 'weekly', now - timedelta(hours=2))
        with patch.object(gmail, 'LAST_SENT_FILE', path):
            start = gmail.fetch_window_start('weekly')

    assert now - start >= timedelta(days=6, hours=23), 'weekly-venster is korter dan 7 dagen'
    print('  PASS test_lookback_weekly_floor_is_a_week')


def test_lookback_falls_back_when_file_missing():
    """Ontbrekend last_sent.json valt terug op de ondergrens in plaats van te crashen."""
    from datetime import datetime, timedelta, timezone
    from src import gmail

    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(gmail, 'LAST_SENT_FILE', Path(tmp) / 'bestaat-niet.json'):
            start = gmail.fetch_window_start('daily')

    assert now - start >= timedelta(hours=23, minutes=59)
    print('  PASS test_lookback_falls_back_when_file_missing')


# ---------------------------------------------------------------------------
# Verzending: reconnect na een gebroken SMTP-verbinding, en eerlijk tellen
# ---------------------------------------------------------------------------

def _patch_mailer_io(subscribers: list[str]):
    """Patch alles wat send_newsletter buiten SMTP om aanraakt."""
    return [
        patch('src.mailer.get_subscribers', return_value=subscribers),
        patch('src.mailer.get_mailerlog', return_value=set()),
        patch('src.mailer.mailerlog'),
        patch('src.mailer.delete_email', return_value=True),
        patch('src.mailer.update_last_sent_timestamp'),
        patch('src.mailer.time.sleep'),
    ]


def test_smtp_reconnects_after_broken_connection():
    """Incident 08:46: verbinding valt weg. Verwacht reconnect, iedereen bezorgd."""
    import smtplib
    from contextlib import ExitStack
    from src import mailer

    subscribers = [f'user{i}@example.com' for i in range(5)]
    dead, fresh = MagicMock(), MagicMock()
    # De eerste server bezorgt er twee en valt dan om, precies zoals op 25 augustus.
    dead.sendmail.side_effect = [None, None, smtplib.SMTPServerDisconnected('reset by peer')]

    with ExitStack() as stack:
        for p in _patch_mailer_io(subscribers):
            stack.enter_context(p)
        connect = stack.enter_context(
            patch('src.mailer.connect_smtp', side_effect=[dead, fresh]))
        log = stack.enter_context(patch('src.mailer.lg'))
        mailer.send_newsletter('daily', '<html>[EMAIL]</html>', 'Titel')

    assert connect.call_count == 2, f'verwacht een reconnect, kreeg {connect.call_count} verbinding(en)'
    assert fresh.sendmail.call_count == 3, f'verse verbinding bezorgde er {fresh.sendmail.call_count}, verwacht 3'
    assert not log.error.called, f'geslaagde retry mag geen ERROR loggen: {log.error.call_args_list}'
    assert log.warning.called, 'een reconnect hoort een WARNING te loggen'
    print('  PASS test_smtp_reconnects_after_broken_connection')


def test_smtp_reports_actual_count_not_subscriber_count():
    """Incident: log meldde 'Sent to 56 recipients' terwijl er 29 aankwamen."""
    import smtplib
    from contextlib import ExitStack
    from src import mailer

    subscribers = [f'user{i}@example.com' for i in range(4)]
    server = MagicMock()
    # user2 faalt structureel: eerste poging en de retry na reconnect.
    def sendmail(_from, to, _msg):
        if to == ['user2@example.com']:
            raise smtplib.SMTPRecipientsRefused({to[0]: (550, b'no such user')})
    server.sendmail.side_effect = sendmail

    with ExitStack() as stack:
        for p in _patch_mailer_io(subscribers):
            stack.enter_context(p)
        stack.enter_context(patch('src.mailer.connect_smtp', return_value=server))
        log = stack.enter_context(patch('src.mailer.lg'))
        mailer.send_newsletter('daily', '<html>[EMAIL]</html>', 'Titel')

    completed = ' '.join(str(c) for c in log.info.call_args_list)
    assert 'Sent to 3 of 4' in completed, f'oneerlijke telling in afsluitregel: {completed}'

    errors = ' '.join(str(c) for c in log.error.call_args_list)
    assert 'user2@example.com' in errors, 'gemiste abonnee wordt niet naar Janitor gemeld'
    assert '--resend' in errors, 'foutmelding noemt de herstelactie niet'
    print('  PASS test_smtp_reports_actual_count_not_subscriber_count')


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
        test_publishable_rejects_incident_2026_08_25,
        test_publishable_accepts_normal_newsletter,
        test_publishable_rejects_too_few_articles,
        test_publishable_accepts_newsletter_without_links,
        test_publishable_rejects_empty_summary,
        test_lookback_floor_when_last_send_was_recent,
        test_lookback_uses_last_sent_when_older_than_floor,
        test_lookback_weekly_floor_is_a_week,
        test_lookback_falls_back_when_file_missing,
        test_smtp_reconnects_after_broken_connection,
        test_smtp_reports_actual_count_not_subscriber_count,
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
