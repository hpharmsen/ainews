"""Lichtgewicht test-runner. Draai met `python tests/main.py`."""
import json
import os
import sys
import tempfile
import time
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
# Bronlinks: de nieuwsbrief van 8 september bevatte er nul
# ---------------------------------------------------------------------------

def _mail_with_parts(plain: str, html: str):
    """Een Mail met een gemockte IMAP-verbinding die deze ene mail teruggeeft."""
    from email.message import EmailMessage
    from src.gmail import Mail

    msg = EmailMessage()
    msg['Subject'] = 'Nieuwsbrief'
    msg.set_content(plain)
    msg.add_alternative(html, subtype='html')

    mail = Mail()
    mail.mail = MagicMock()
    mail.mail.uid.return_value = ('OK', [(b'1 (RFC822', msg.as_bytes())])
    return mail


def test_body_falls_back_to_html_when_plaintext_has_no_links():
    """AlphaSignal levert een plaintext-variant zonder bronlinks. Dan moet de HTML gebruikt."""
    plain = 'Top News\nClaude verifieert Fermat in 13M regels\nDeepMind swarm splitst zich'
    html = '<p><a href="https://app.alphasignal.ai/c?uid=abc">Claude verifieert Fermat</a></p>'

    body = _mail_with_parts(plain, html).get_email_body('1')

    assert 'https://app.alphasignal.ai/c?uid=abc' in body, f'link ontbreekt in body: {body!r}'
    print('  PASS test_body_falls_back_to_html_when_plaintext_has_no_links')


def test_body_prefers_plaintext_when_it_has_links():
    """Beehiiv-bronnen leveren markdown met links; die compacte versie houden we."""
    plain = ('[Item een](https://example.com/1)\n'
             '[Item twee](https://example.com/2)\n'
             '[Item drie](https://example.com/3)')
    html = '<p><a href="https://example.com/anders">Iets anders</a></p>'

    body = _mail_with_parts(plain, html).get_email_body('1')

    assert body == plain + '\n', f'plaintext verwacht, kreeg: {body!r}'
    print('  PASS test_body_prefers_plaintext_when_it_has_links')


def test_body_cap_keeps_the_news_not_just_the_ad_header():
    """2000 tekens hield alleen de menubalk en het advertentieblok over."""
    from src import gmail

    assert gmail.MAX_LEN_PER_MAIL >= 8000, f'cap {gmail.MAX_LEN_PER_MAIL} knipt het nieuws eraf'
    assert gmail.MAX_TOTAL_LEN >= gmail.MAX_LEN_PER_MAIL * 5, 'te weinig ruimte voor vijf bronnen'
    print('  PASS test_body_cap_keeps_the_news_not_just_the_ad_header')


def test_personal_tracking_links_are_recognized():
    """Een doorstuurlink draagt HP's abonneenummer mee en mag de nieuwsbrief niet in."""
    from src.ai import has_personal_tracking

    assert has_personal_tracking('https://app.alphasignal.ai/c?uid=2eAy&lid=1u7O')
    assert has_personal_tracking('https://recs.page/offers?lc=x&email=hp@harmsen.nl')
    assert not has_personal_tracking('https://www.anthropic.com/research/fermat')
    assert not has_personal_tracking('https://example.com/post?utm_source=news')
    print('  PASS test_personal_tracking_links_are_recognized')


# ---------------------------------------------------------------------------
# Classificatie van post die binnenkomt op het nieuwsbriefadres
# ---------------------------------------------------------------------------

# De echte mail van Elise. Klinkt als een afmelding, is het tegenovergestelde.
ELISE = 'Hoi HP, als ik me niet vergis ontvang ik de nieuwsbrief sinds 10 april niet meer.'


def _classify_model(choice: str, confidence: float) -> MagicMock:
    """Een gemockt System One model dat deze ene keuze teruggeeft."""
    model = MagicMock()
    model.classify.return_value = {
        'type': 'choice',
        'choice': choice,
        'confidence': confidence,
        'probabilities': {choice: confidence},
    }
    return model


def test_classify_keeps_a_delivery_complaint_away_from_unsubscribing():
    """AE2: een klacht dat de nieuwsbrief niet aankomt is werk voor HP, geen afmelding."""
    from src.ai import classify_reply

    model = _classify_model('hp', 1.0)
    with patch('src.ai.Model', return_value=model):
        category = classify_reply('elise@idest.nl', "Re: HP's AI daily - 6 april", ELISE)

    assert category == 'hp', f'verwacht hp, kreeg {category}'
    print('  PASS test_classify_keeps_a_delivery_complaint_away_from_unsubscribing')


def test_classify_falls_back_to_hp_below_the_confidence_threshold():
    """R5: onder de drempel telt de keuze van het model niet, want twijfel wordt hp."""
    from src.ai import MIN_CONFIDENCE, classify_reply

    model = _classify_model('afmelding', MIN_CONFIDENCE - 0.01)
    with patch('src.ai.Model', return_value=model):
        category = classify_reply('iemand@example.com', 'stoppen', 'Graag stoppen.')

    assert category == 'hp', f'twijfel moet hp worden, kreeg {category}'
    print('  PASS test_classify_falls_back_to_hp_below_the_confidence_threshold')


def test_classify_accepts_confidence_exactly_on_the_threshold():
    """De drempel zelf is genoeg. Anders schuift hij stil een stap op."""
    from src.ai import MIN_CONFIDENCE, classify_reply

    model = _classify_model('afmelding', MIN_CONFIDENCE)
    with patch('src.ai.Model', return_value=model):
        category = classify_reply('iemand@example.com', 'stoppen', 'Graag stoppen.')

    assert category == 'afmelding', f'verwacht afmelding, kreeg {category}'
    print('  PASS test_classify_accepts_confidence_exactly_on_the_threshold')


def test_classify_offers_all_four_categories():
    """R4: het model moet uit precies deze vier kunnen kiezen, niet uit minder."""
    from src.ai import classify_reply

    model = _classify_model('hp', 1.0)
    with patch('src.ai.Model', return_value=model):
        classify_reply('iemand@example.com', 'iets', 'iets')

    options = model.classify.call_args.args[1]
    assert set(options) == {'afmelding', 'delay', 'bounce', 'hp'}, f'opties waren {sorted(options)}'
    assert all(options.values()), 'elke categorie heeft een omschrijving nodig'
    print('  PASS test_classify_offers_all_four_categories')


def test_classify_truncates_a_long_body():
    """Een doorgestuurde nieuwsbrief in een reply mag de aanroep niet opblazen."""
    from src.ai import MAX_REPLY_BODY, classify_reply

    model = _classify_model('hp', 1.0)
    with patch('src.ai.Model', return_value=model):
        classify_reply('iemand@example.com', 'lang', 'z' * (MAX_REPLY_BODY + 5000))

    state = model.classify.call_args.args[0]
    assert state.count('z') == MAX_REPLY_BODY, f'{state.count("z")} tekens doorgelaten'
    print('  PASS test_classify_truncates_a_long_body')


def test_classify_handles_an_empty_body():
    """Een one-click afmelding heeft soms alleen een subject."""
    from src.ai import classify_reply

    model = _classify_model('afmelding', 1.0)
    with patch('src.ai.Model', return_value=model):
        category = classify_reply('iemand@example.com', 'unsubscribe', None)

    assert category == 'afmelding', f'verwacht afmelding, kreeg {category}'
    assert model.classify.called, 'een lege body mag de aanroep niet overslaan'
    print('  PASS test_classify_handles_an_empty_body')


def test_classify_lets_a_model_failure_through():
    """R15 wordt door de afhandelaar afgedekt, niet hier. De fout moet naar buiten komen."""
    from justai.models.basemodel import ConnectionException

    from src.ai import classify_reply

    model = MagicMock()
    model.classify.side_effect = ConnectionException('geen verbinding')
    with patch('src.ai.Model', return_value=model):
        try:
            classify_reply('iemand@example.com', 'iets', 'iets')
        except ConnectionException:
            pass
        else:
            raise AssertionError('ConnectionException had door moeten komen')
    print('  PASS test_classify_lets_a_model_failure_through')


# ---------------------------------------------------------------------------
# Visual-selectie: Jev kiest, de code dwingt twee verschillende artikelen af
# ---------------------------------------------------------------------------

def _visuals_answer(illustratie: dict[int, float], infographic: dict[int, float]) -> dict:
    """Een Jev-antwoord op beide vragen, met kansen per artikelindex."""
    def answer(probs: dict[int, float]) -> dict:
        best = max(probs, key=probs.get)
        return {'type': 'choice', 'choice': str(best), 'confidence': probs[best],
                'probabilities': {str(i): p for i, p in probs.items()}}
    return {'illustratie': answer(illustratie), 'infographic': answer(infographic)}


def _select_visuals(articles: list[dict], *answers):
    """Draai select_articles_for_visuals met een gemockte Jev die deze antwoorden geeft."""
    from src.ai import select_articles_for_visuals

    model = MagicMock()
    model.classify.side_effect = list(answers)
    with patch('src.ai.Model', return_value=model), patch('src.ai.lg') as log:
        result = select_articles_for_visuals(articles)
    return result, model, log


def test_visuals_takes_the_choice_of_jev():
    """R2: elke vraag krijgt het artikel dat Jev het meest geschikt vindt."""
    answer = _visuals_answer({2: 0.8, 0: 0.2}, {4: 0.9, 2: 0.1})
    result, _, _ = _select_visuals(_articles(5), answer)

    assert result == {'image_article': 2, 'infographic_article': 4}, f'kreeg {result}'
    print('  PASS test_visuals_takes_the_choice_of_jev')


def test_visuals_gives_the_infographic_the_runner_up_on_a_clash():
    """R1 en R2: kiezen beide vragen hetzelfde artikel, dan krijgt de infographic de op één na beste."""
    answer = _visuals_answer({1: 1.0, 3: 0.0}, {1: 1.0, 3: 0.6, 0: 0.2})
    result, _, _ = _select_visuals(_articles(5), answer)

    assert result == {'image_article': 1, 'infographic_article': 3}, f'kreeg {result}'
    print('  PASS test_visuals_gives_the_infographic_the_runner_up_on_a_clash')


def test_visuals_breaks_a_tie_on_the_lowest_index():
    """KTD2: staan de overige kansen gelijk, dan wint de laagste index."""
    answer = _visuals_answer({0: 1.0, 1: 0.0, 2: 0.0, 3: 0.0}, {0: 1.0, 3: 0.0, 2: 0.0, 1: 0.0})
    result, _, _ = _select_visuals(_articles(4), answer)

    assert result == {'image_article': 0, 'infographic_article': 1}, f'kreeg {result}'
    print('  PASS test_visuals_breaks_a_tie_on_the_lowest_index')


def test_visuals_asks_two_choice_questions_over_all_articles():
    """KTD1: één aanroep met twee keuzevragen, één optie per artikel met de index als sleutel."""
    articles = _articles(5)
    _, model, _ = _select_visuals(articles, _visuals_answer({0: 1.0}, {1: 1.0}))

    assert model.classify.call_count == 1, f'{model.classify.call_count} aanroepen'
    questions = model.classify.call_args.kwargs['questions']
    assert set(questions) == {'illustratie', 'infographic'}, f'vragen waren {sorted(questions)}'
    for name, question in questions.items():
        assert question['type'] == 'choice', f'{name} is geen choice'
        assert set(question['criteria']) == {str(i) for i in range(5)}, f'opties van {name}: {question["criteria"]}'
        for i, article in enumerate(articles):
            assert article['title'] in question['criteria'][str(i)], f'titel {i} ontbreekt bij {name}'
    assert model.classify.call_args.kwargs.get('cached') is False, 'KTD5: niet cachen'
    print('  PASS test_visuals_asks_two_choice_questions_over_all_articles')


def test_visuals_retries_once_on_a_failure():
    """KTD4: één netwerkhik geeft een WARNING en een tweede poging, geen alarm."""
    from justai.models.basemodel import ConnectionException

    answer = _visuals_answer({2: 1.0}, {3: 1.0})
    result, _, log = _select_visuals(_articles(4), ConnectionException('hik'), answer)

    assert result == {'image_article': 2, 'infographic_article': 3}, f'kreeg {result}'
    assert log.warning.call_count == 1, f'{log.warning.call_count} warnings'
    assert not log.error.called, f'een geslaagde herhaling mag geen ERROR loggen: {log.error.call_args_list}'
    print('  PASS test_visuals_retries_once_on_a_failure')


def test_visuals_falls_back_after_two_failures():
    """R4: faalt Jev twee keer, dan artikel 0 en 1, een lg.error, en de run gaat door."""
    from justai.models.basemodel import ConnectionException

    result, _, log = _select_visuals(
        _articles(4), ConnectionException('weg'), ConnectionException('nog steeds weg'))

    assert result == {'image_article': 0, 'infographic_article': 1}, f'kreeg {result}'
    assert log.error.call_count == 1, f'{log.error.call_count} errors'
    print('  PASS test_visuals_falls_back_after_two_failures')


def test_visuals_falls_back_on_a_malformed_answer():
    """KTD6: een antwoord zonder de vraag infographic valt onder dezelfde vangst."""
    broken = {'illustratie': _visuals_answer({0: 1.0}, {1: 1.0})['illustratie']}
    result, _, log = _select_visuals(_articles(4), broken, broken)

    assert result == {'image_article': 0, 'infographic_article': 1}, f'kreeg {result}'
    assert log.error.call_count == 1, f'{log.error.call_count} errors'
    print('  PASS test_visuals_falls_back_on_a_malformed_answer')


def test_visuals_picks_two_valid_articles_from_three():
    """R1: bij het minimum van 3 artikelen zijn beide indexen geldig en verschillend."""
    answer = _visuals_answer({2: 1.0, 0: 0.0, 1: 0.0}, {2: 0.9, 0: 0.05, 1: 0.05})
    result, _, _ = _select_visuals(_articles(3), answer)

    image, infographic = result['image_article'], result['infographic_article']
    assert image != infographic, f'twee keer artikel {image}'
    assert {image, infographic} <= {0, 1, 2}, f'ongeldige index in {result}'
    print('  PASS test_visuals_picks_two_valid_articles_from_three')


# ---------------------------------------------------------------------------
# Abonnee-lookup: een databasestoring is niet hetzelfde als "adres bestaat niet"
# ---------------------------------------------------------------------------

def _subscriber_table():
    """Een echte tabeldefinitie, zodat select() een geldige query kan bouwen."""
    from sqlalchemy import Column, DateTime, MetaData, String, Table

    return Table('nieuwsbrief_subscriber', MetaData(),
                 Column('email', String), Column('status', String), Column('updated_at', DateTime))


def _patch_subscriber_db(row=None, error: Exception | None = None):
    """Patch subscribers.db zodat de query deze rij oplevert, of deze fout."""
    from contextlib import contextmanager

    @contextmanager
    def fake_db():
        conn = MagicMock()
        if error is not None:
            conn.execute.side_effect = error
        else:
            conn.execute.return_value.fetchone.return_value = row
        yield conn, {'nieuwsbrief_subscriber': _subscriber_table()}

    return patch('src.subscribers.db', fake_db)


def test_subscriber_lookup_raises_on_a_database_failure():
    """KTD5: stil None teruggeven zou elke afmelding ongemerkt laten verdwijnen."""
    from src.subscribers import get_subscriber_status

    with _patch_subscriber_db(error=RuntimeError('connection refused')):
        try:
            result = get_subscriber_status('iemand@example.com')
        except RuntimeError:
            pass
        else:
            raise AssertionError(f'databasefout werd geslikt, kreeg {result!r}')
    print('  PASS test_subscriber_lookup_raises_on_a_database_failure')


def test_subscriber_lookup_returns_none_for_an_unknown_address():
    """None betekent voortaan uitsluitend: geen rij gevonden. Zonder lg.error."""
    from src.subscribers import get_subscriber_status

    with _patch_subscriber_db(row=None), patch('src.subscribers.lg') as lg_mock:
        result = get_subscriber_status('onbekend@example.com')

    assert result is None, f'verwacht None, kreeg {result!r}'
    assert not lg_mock.error.called, f'onverwachte lg.error: {lg_mock.error.call_args}'
    print('  PASS test_subscriber_lookup_returns_none_for_an_unknown_address')


def test_subscriber_lookup_still_returns_status_and_timestamp():
    """Het bestaande gedrag voor een bekend adres blijft ongewijzigd."""
    from datetime import datetime, timezone

    from src.subscribers import get_subscriber_status

    moment = datetime(2025, 11, 26, 20, 33, tzinfo=timezone.utc)
    with _patch_subscriber_db(row=('daily', moment)):
        result = get_subscriber_status('amy.klewis@hotmail.co.uk')

    assert result == {'status': 'daily', 'updated_at': moment}, f'kreeg {result!r}'
    print('  PASS test_subscriber_lookup_still_returns_status_and_timestamp')


# ---------------------------------------------------------------------------
# Afhandelaar van het label nieuwsbrief
# ---------------------------------------------------------------------------

MIGADU_BOUNCE = """This is the mail system at host rel11.migadu.com.

I'm sorry to have to inform you that your message could not
be delivered to one or more recipients.

<jeroen@nas.nl>: host mx3.hostghost.nl[154.59.104.23] said: 550 Sender's policy
    prohibits this message: Reject (in reply to end of DATA command)
"""


def _reply_message(sender: str, subject: str, body: str,
                   date: str = 'Wed, 23 Sep 2026 06:59:53 +0000'):
    """Een echte mail, zodat de headerverwerking niet wegvalt achter een mock."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg['From'] = sender
    msg['Subject'] = subject
    msg['Date'] = date
    msg.set_content(body)
    return msg


def _fake_mail(messages: dict, delete_ok: bool = True, connected: bool = True):
    """Een Mail-dubbel met deze berichten in het label, met de echte bounce-extractie."""
    from src.gmail import Mail

    mail = MagicMock(spec=Mail)
    mail.connect.return_value = connected
    mail.mail = MagicMock()
    mail.mail.select.return_value = ('OK', [b''])
    mail.mail.uid.return_value = ('OK', [' '.join(messages).encode()])
    mail.get_message.side_effect = messages.get
    mail.delete_email.return_value = delete_ok
    mail._extract_original_recipient.side_effect = (
        lambda msg: Mail._extract_original_recipient(mail, msg))
    return mail


def _run_handler(tmpdir: Path, mail, category, *, status=None, seen=None):
    """Draai handle_replies() met alles buiten IMAP en het model afgevangen.

    Geeft (update_calls, undelivered_data, logregels, weggeschreven tijdstempel) terug.
    """
    import src.replies as replies

    seen_file = tmpdir / 'replies_seen.json'
    if seen is not None:
        seen_file.write_text(json.dumps({'last_pass': seen}))
    log_file = tmpdir / 'replieslog.txt'
    undelivered = tmpdir / 'undelivered.json'

    classify = category if callable(category) else (lambda *a, **k: category)
    update = MagicMock(return_value=True)

    with patch.object(replies, 'Mail', return_value=mail), \
            patch.object(replies, 'SEEN_FILE', seen_file), \
            patch.object(replies, 'LOG_FILE', log_file), \
            patch.object(replies, 'classify_reply', side_effect=classify) as classify_mock, \
            patch.object(replies, 'get_subscriber_status', return_value=status), \
            patch.object(replies, 'update_subscription', update), \
            patch('src.undelivered.undelivered_file', undelivered), \
            patch('src.replies.mark_undeliverable') as mark_mock:
        replies.handle_replies()

    lines = log_file.read_text(encoding='utf-8').splitlines() if log_file.exists() else []
    counts = json.loads(undelivered.read_text()) if undelivered.exists() else {}
    stamp = json.loads(seen_file.read_text())['last_pass'] if seen_file.exists() else None
    return {'update': update, 'counts': counts, 'lines': lines, 'stamp': stamp,
            'classify': classify_mock, 'mark': mark_mock}


def test_replies_unsubscribes_a_known_address():
    """AE1: afmelding van een bekend adres levert unsubscribed, verwijderen en een logregel."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'3': _reply_message('amy.klewis@hotmail.co.uk', 'unsubscribe',
                                               'Apple Mail sent this email to unsubscribe')})
        r = _run_handler(Path(tmp), mail, 'afmelding', status={'status': 'daily', 'updated_at': None})

    r['update'].assert_called_once_with('amy.klewis@hotmail.co.uk', 'unsubscribed')
    mail.delete_email.assert_called_once_with('3', folder='nieuwsbrief')
    assert len(r['lines']) == 1, f'verwacht 1 logregel, kreeg {r["lines"]}'
    assert 'amy.klewis@hotmail.co.uk' in r['lines'][0] and 'afmelding' in r['lines'][0]
    print('  PASS test_replies_unsubscribes_a_known_address')


def test_replies_drops_an_unknown_unsubscribe_silently():
    """AE7: onbekend adres levert geen databaseschrijfactie en geen lg.error."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'7': _reply_message('vreemd@example.com', 'unsubscribe', 'stop maar')})
        with patch('src.replies.lg') as lg_mock:
            r = _run_handler(Path(tmp), mail, 'afmelding', status=None)

    assert not r['update'].called, 'onbekend adres mag niet uitgeschreven worden'
    mail.delete_email.assert_called_once_with('7', folder='nieuwsbrief')
    assert len(r['lines']) == 1, f'verwacht 1 logregel, kreeg {r["lines"]}'
    assert not lg_mock.error.called, f'onverwachte lg.error: {lg_mock.error.call_args}'
    print('  PASS test_replies_drops_an_unknown_unsubscribe_silently')


def test_replies_deletes_a_delay_without_counting_it():
    """AE3: een delay-melding verdwijnt, maar raakt de bouncetelling niet."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'2': _reply_message('mailer-daemon@googlemail.com',
                                               'Delivery Status Notification (Delay)',
                                               'will retry for 46 more hours hero@hetab.org')})
        r = _run_handler(Path(tmp), mail, 'delay')

    mail.delete_email.assert_called_once_with('2', folder='nieuwsbrief')
    assert r['counts'] == {}, f'delay mag niet meetellen, kreeg {r["counts"]}'
    assert not r['mark'].called, 'delay mag niemand op undeliverable zetten'
    assert len(r['lines']) == 1
    print('  PASS test_replies_deletes_a_delay_without_counting_it')


def test_replies_counts_a_permanent_bounce():
    """AE4: een 5xx-bounce verhoogt permanent_count voor het geextraheerde adres."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'15': _reply_message('MAILER-DAEMON@migadu.com',
                                                'Undelivered Mail Returned to Sender',
                                                MIGADU_BOUNCE)})
        r = _run_handler(Path(tmp), mail, 'bounce')

    assert 'jeroen@nas.nl' in r['counts'], f'adres niet geteld, kreeg {r["counts"]}'
    entry = r['counts']['jeroen@nas.nl']
    assert entry['permanent_count'] == 1, f'permanent_count is {entry["permanent_count"]}'
    assert entry['count'] == 1, f'count is {entry["count"]}'
    mail.delete_email.assert_called_once_with('15', folder='nieuwsbrief')
    print('  PASS test_replies_counts_a_permanent_bounce')


def test_replies_does_not_count_a_bounce_it_could_not_delete():
    """Tellen na verwijderen, anders telt de volgende pass dezelfde bounce nog eens."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'15': _reply_message('MAILER-DAEMON@migadu.com', 'Undelivered',
                                                MIGADU_BOUNCE)}, delete_ok=False)
        r = _run_handler(Path(tmp), mail, 'bounce')

    assert r['counts'] == {}, f'mislukte verwijdering mag niet tellen, kreeg {r["counts"]}'
    assert r['lines'] == [], f'geen actie, dus geen logregel, kreeg {r["lines"]}'
    print('  PASS test_replies_does_not_count_a_bounce_it_could_not_delete')


def test_replies_leaves_a_bounce_without_a_recipient_alone():
    """Zonder ontvangeradres valt er niets te tellen, dus blijft het bericht staan."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'9': _reply_message('MAILER-DAEMON@example.com', 'Undelivered',
                                               'iets ging mis, geen adres te vinden')})
        r = _run_handler(Path(tmp), mail, 'bounce')

    assert not mail.delete_email.called, 'bericht had moeten blijven staan'
    assert r['counts'] == {}, f'niets te tellen, kreeg {r["counts"]}'
    print('  PASS test_replies_leaves_a_bounce_without_a_recipient_alone')


def test_replies_skips_a_message_older_than_the_timestamp():
    """AE6: wat gisteren al bekeken is krijgt vandaag geen tweede gok."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'4': _reply_message('elise@idest.nl', 'Re: daily', ELISE,
                                               date='Sat, 18 Apr 2026 15:31:28 +0200')})
        r = _run_handler(Path(tmp), mail, 'hp', seen='2026-09-01T00:00:00+00:00')

    assert not r['classify'].called, 'oud bericht mag geen modelaanroep kosten'
    assert not mail.delete_email.called
    print('  PASS test_replies_skips_a_message_older_than_the_timestamp')


def test_replies_skips_its_own_mail():
    """R2: verzonden nieuwsbrieven en HP's eigen antwoorden zitten ook in het label."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({
            '1': _reply_message('nieuwsbrief@harmsen.nl', "HP's AI weekly - week 39", 'de brief'),
            '7': _reply_message('hp@harmsen.nl', 'Re: HP AI daily', 'mijn antwoord'),
        })
        r = _run_handler(Path(tmp), mail, 'afmelding')

    assert not r['classify'].called, 'eigen post mag geen modelaanroep kosten'
    assert not mail.delete_email.called, 'eigen post moet blijven staan'
    print('  PASS test_replies_skips_its_own_mail')


def test_replies_survives_a_failing_classification():
    """AE5: één lg.error, het tijdstempel blijft staan, en de run gaat door."""
    from justai.models.basemodel import ConnectionException

    def boom(*args, **kwargs):
        raise ConnectionException('model onbereikbaar')

    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'3': _reply_message('iemand@example.com', 'unsubscribe', 'stop')})
        with patch('src.replies.lg') as lg_mock:
            r = _run_handler(Path(tmp), mail, boom, seen='2026-01-01T00:00:00+00:00')

    assert lg_mock.error.call_count == 1, f'verwacht 1 lg.error, kreeg {lg_mock.error.call_count}'
    assert r['stamp'] == '2026-01-01T00:00:00+00:00', f'tijdstempel schoof op naar {r["stamp"]}'
    assert not mail.delete_email.called
    print('  PASS test_replies_survives_a_failing_classification')


def test_replies_survives_a_failing_imap_connection():
    """Geen exception en geen exit(): de nieuwsbrief moet gewoon uit kunnen."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({}, connected=False)
        with patch('src.replies.lg') as lg_mock:
            r = _run_handler(Path(tmp), mail, 'hp', seen='2026-01-01T00:00:00+00:00')

    assert lg_mock.error.called, 'een mislukte verbinding hoort een lg.error te geven'
    assert r['stamp'] == '2026-01-01T00:00:00+00:00', 'tijdstempel mag niet opschuiven'
    print('  PASS test_replies_survives_a_failing_imap_connection')


def test_replies_advances_the_timestamp_on_an_empty_label():
    """Een leeg label is geen fout, en de pass is wel geslaagd."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({})
        with patch('src.replies.lg') as lg_mock:
            r = _run_handler(Path(tmp), mail, 'hp', seen='2026-01-01T00:00:00+00:00')

    assert not lg_mock.error.called, f'leeg label gaf lg.error: {lg_mock.error.call_args}'
    assert r['stamp'] != '2026-01-01T00:00:00+00:00', 'tijdstempel had moeten opschuiven'
    print('  PASS test_replies_advances_the_timestamp_on_an_empty_label')


def test_replies_processes_everything_without_a_timestamp_file():
    """Zonder data/replies_seen.json is de hele achterstand nieuw."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'12': _reply_message('elise@idest.nl', 'Re: daily', ELISE,
                                                date='Mon, 20 Oct 2025 13:53:06 +0200')})
        r = _run_handler(Path(tmp), mail, 'hp', seen=None)

    assert r['classify'].call_count == 1, 'oude post moet alsnog langs het model'
    print('  PASS test_replies_processes_everything_without_a_timestamp_file')


def test_replies_leaves_hp_mail_untouched():
    """R11: werk voor HP blijft staan en levert geen logregel."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'4': _reply_message('elise@idest.nl', 'Re: daily', ELISE)})
        r = _run_handler(Path(tmp), mail, 'hp')

    assert not mail.delete_email.called, 'hp-post mag niet verwijderd worden'
    assert not r['update'].called
    assert r['lines'] == [], f'hp hoort geen logregel te geven, kreeg {r["lines"]}'
    print('  PASS test_replies_leaves_hp_mail_untouched')


def test_replies_keeps_a_log_line_on_one_line():
    """R12: het tekstfragment is vrije tekst, dus regelafbrekingen moeten eruit."""
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'3': _reply_message('iemand@example.com', 'unsubscribe',
                                               'Regel een\nRegel twee\r\nRegel drie')})
        r = _run_handler(Path(tmp), mail, 'afmelding', status={'status': 'daily', 'updated_at': None})

    assert len(r['lines']) == 1, f'verwacht 1 regel, kreeg {len(r["lines"])}: {r["lines"]}'
    assert r['lines'][0].count('\t') == 4, f'verwacht 5 velden, kreeg {r["lines"][0]!r}'
    assert 'Regel een Regel twee Regel drie' in r['lines'][0], r['lines'][0]
    print('  PASS test_replies_keeps_a_log_line_on_one_line')


def test_replies_stamps_the_start_of_the_pass():
    """KTD4: post die tijdens de pass binnenkomt moet de volgende keer alsnog gezien worden."""
    from datetime import datetime, timezone

    import src.replies as replies

    before = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        mail = _fake_mail({'4': _reply_message('elise@idest.nl', 'Re: daily', ELISE)})
        # get_message vertraagt de pass, zodat begin en eind aantoonbaar verschillen.
        real_get = mail.get_message.side_effect

        def slow(uid):
            time.sleep(0.2)
            return real_get(uid)

        mail.get_message.side_effect = slow
        r = _run_handler(Path(tmp), mail, 'hp')
    after = datetime.now(timezone.utc)

    stamp = datetime.fromisoformat(r['stamp'])
    assert before <= stamp, f'tijdstempel {stamp} ligt voor het begin van de pass'
    assert (after - stamp).total_seconds() >= 0.2, (
        f'tijdstempel {stamp} is het eind van de pass, niet het begin')
    assert replies.SEEN_FILE.name == 'replies_seen.json'
    print('  PASS test_replies_stamps_the_start_of_the_pass')


# ---------------------------------------------------------------------------
# Bounces: één ingang, en geen pad dat het proces kan afbreken
# ---------------------------------------------------------------------------

def test_the_inbox_bounce_route_is_gone():
    """KTD7: de oude ingang zocht op INBOX en kon met exit() de hele run stoppen."""
    root = Path(__file__).resolve().parent.parent
    undelivered = (root / 'src' / 'undelivered.py').read_text()
    gmail = (root / 'src' / 'gmail.py').read_text()
    main_py = (root / 'main.py').read_text()

    assert 'exit(' not in undelivered, 'src/undelivered.py bevat nog een exit()'
    for gone in ('def get_mail(', 'def get_undelivered_emails(',
                 'def delete_emails(', 'def handle_undelivered('):
        assert gone not in undelivered, f'{gone} staat nog in src/undelivered.py'
    assert 'def get_undelivered(' not in gmail, 'Mail.get_undelivered() bestaat nog'
    assert 'handle_undelivered' not in main_py, 'main.py verwijst nog naar handle_undelivered'

    # Deze twee blijven: de afhandelaar leunt erop.
    assert 'def _extract_original_recipient(' in gmail
    assert 'def parse_undelivered_emails(' in undelivered
    assert 'def mark_undeliverable(' in undelivered
    print('  PASS test_the_inbox_bounce_route_is_gone')


def test_bounce_counting_is_unchanged():
    """R9 houdt de telfuncties ongewijzigd: 2 voor 5xx, 5 voor 4xx, spam telt niet mee."""
    from justdays import Day

    today = str(Day())
    with tempfile.TemporaryDirectory() as tmp:
        counts_file = Path(tmp) / 'undelivered.json'
        with patch('src.undelivered.undelivered_file', counts_file):
            from src.undelivered import parse_undelivered_emails

            to_delete, to_mark = parse_undelivered_emails([
                {'email_id': '1', 'recipient_email': 'dood@example.com', 'is_permanent': True},
                {'email_id': '2', 'recipient_email': 'vol@example.com', 'is_permanent': False},
                {'email_id': '3', 'recipient_email': 'spam@example.com', 'is_spam_rejection': True},
            ])
            first = json.loads(counts_file.read_text())
            # Tweede 5xx op hetzelfde adres raakt de drempel van 2.
            _, second_mark = parse_undelivered_emails([
                {'email_id': '4', 'recipient_email': 'dood@example.com', 'is_permanent': True},
            ])

    assert to_delete == ['1', '2', '3'], f'alles moet weg, kreeg {to_delete}'
    assert to_mark == [], f'na één bounce nog niemand undeliverable, kreeg {to_mark}'
    assert first['dood@example.com'] == {'count': 1, 'permanent_count': 1, 'last_bounce': today}
    assert first['vol@example.com'] == {'count': 1, 'permanent_count': 0, 'last_bounce': today}
    assert 'spam@example.com' not in first, 'een spam-rejection mag niet meetellen'
    assert second_mark == ['dood@example.com'], f'drempel 2 niet geraakt, kreeg {second_mark}'
    print('  PASS test_bounce_counting_is_unchanged')


def test_marking_undeliverable_respects_a_resubscribe():
    """Wie zich na zijn laatste bounce opnieuw aanmeldde houdt zijn abonnement."""
    from datetime import datetime

    from src.undelivered import mark_undeliverable

    with tempfile.TemporaryDirectory() as tmp:
        counts_file = Path(tmp) / 'undelivered.json'
        counts_file.write_text(json.dumps(
            {'terug@example.com': {'count': 9, 'permanent_count': 1, 'last_bounce': '2026-01-01'}}))
        with patch('src.undelivered.undelivered_file', counts_file), \
                patch('src.undelivered.get_subscriber_status',
                      return_value={'status': 'daily', 'updated_at': datetime(2026, 6, 1)}), \
                patch('src.undelivered.update_subscription') as update:
            mark_undeliverable(['terug@example.com'])
        remaining = json.loads(counts_file.read_text())

    assert not update.called, 'een heraanmelding na de bounce mag niet undeliverable worden'
    assert remaining == {}, f'de teller had gereset moeten worden, kreeg {remaining}'
    print('  PASS test_marking_undeliverable_respects_a_resubscribe')


# ---------------------------------------------------------------------------
# De afhandelaar in de nieuwsbrief-run: twee passes, en niet bij --dry-run
# ---------------------------------------------------------------------------

def _run_main(dry_run: bool = False, source_emails: int = 2, real_replies: bool = False,
              selection: dict | None = None, image_index: int = 0):
    """Draai main.main() met alles eromheen afgevangen; geeft de aanroeporde terug."""
    from contextlib import ExitStack

    import main as main_module

    calls = []

    def note(name, result=None):
        def fn(*args, **kwargs):
            calls.append(name)
            return result
        return fn

    articles = _articles(3)
    doubles = {
        'cleanup_cache': MagicMock(),
        'parse_command_line': MagicMock(return_value=('daily', False, dry_run)),
        'already_sent_today': MagicMock(return_value=False),
        'get_raw_mail_text': MagicMock(return_value='ruwe tekst'),
        'parse_emails_to_dict': MagicMock(
            return_value={f'bron {i}': 'tekst' for i in range(source_emails)}),
        'generate_ai_summary': MagicMock(return_value=articles),
        'edit_articles': MagicMock(return_value=articles),
        'check_publishable': MagicMock(return_value=None),
        'select_articles_for_visuals': MagicMock(
            return_value=selection or {'image_article': 0, 'infographic_article': 1}),
        'generate_ai_image': MagicMock(return_value=(image_index, 'https://example.com/i.png')),
        'generate_infographic': MagicMock(return_value=(1, 'https://example.com/g.png')),
        'create_html_email': MagicMock(return_value='<html></html>'),
        'add_to_database': MagicMock(),
        'send_newsletter': MagicMock(side_effect=note('send')),
        'time': MagicMock(),
    }
    if not real_replies:
        doubles['handle_replies'] = MagicMock(side_effect=note('replies'))

    with ExitStack() as stack:
        for name, double in doubles.items():
            stack.enter_context(patch.object(main_module, name, double))
        if real_replies:
            # De echte afhandelaar, met een IMAP-verbinding die niet lukt.
            stack.enter_context(patch('src.replies.Mail', return_value=_fake_mail({}, connected=False)))
        main_module.main()
    return calls, doubles


def test_main_runs_the_handler_before_and_after_sending():
    """R13 en R14: een pass vooraf, zodat wie zich gisteren afmeldde niets meer krijgt."""
    calls, _ = _run_main()

    assert calls == ['replies', 'send', 'replies'], f'aanroeporde was {calls}'
    print('  PASS test_main_runs_the_handler_before_and_after_sending')


def test_main_skips_the_handler_on_a_dry_run():
    """R16: geen uitschrijvingen en geen verwijderingen bij --dry-run."""
    calls, _ = _run_main(dry_run=True)

    assert calls == [], f'dry-run mag niets aanroepen, kreeg {calls}'
    print('  PASS test_main_skips_the_handler_on_a_dry_run')


def test_main_runs_the_first_pass_even_when_the_source_gate_stops_the_run():
    """Te weinig bronmails stopt de nieuwsbrief, maar de afmeldingen zijn dan al verwerkt."""
    calls, _ = _run_main(source_emails=1)

    assert calls == ['replies'], f'verwacht alleen de eerste pass, kreeg {calls}'
    print('  PASS test_main_runs_the_first_pass_even_when_the_source_gate_stops_the_run')


def test_main_sends_the_newsletter_when_the_handler_fails():
    """AE5: de echte afhandelaar met een kapotte IMAP-verbinding mag de run niet stoppen."""
    with patch('src.replies.lg') as lg_mock:
        calls, doubles = _run_main(real_replies=True)

    assert doubles['send_newsletter'].called, 'de nieuwsbrief had gewoon uit moeten gaan'
    assert lg_mock.error.called, 'een mislukte pass hoort een lg.error te geven'
    print('  PASS test_main_sends_the_newsletter_when_the_handler_fails')


def _infographic_index(selection: dict, image_index: int) -> int:
    """De infographic-index die main na het naar voren schuiven van het image-artikel doorgeeft."""
    _, doubles = _run_main(selection=selection, image_index=image_index)
    return doubles['generate_infographic'].call_args.kwargs['visual_selection']['infographic_article']


def test_main_shifts_the_infographic_behind_the_moved_image_article():
    """Artikel 0 schuift een plek op als artikel 2 naar voren gaat."""
    index = _infographic_index({'image_article': 2, 'infographic_article': 0}, image_index=2)
    assert index == 1, f'verwacht 1, kreeg {index}'
    print('  PASS test_main_shifts_the_infographic_behind_the_moved_image_article')


def test_main_keeps_the_infographic_index_when_the_image_stays_first():
    index = _infographic_index({'image_article': 0, 'infographic_article': 1}, image_index=0)
    assert index == 1, f'verwacht 1, kreeg {index}'
    print('  PASS test_main_keeps_the_infographic_index_when_the_image_stays_first')


def test_main_handles_a_cached_image_that_ignores_the_selection():
    """Bij --cached geeft generate_ai_image altijd 0 terug, ook als de selectie iets anders zei."""
    index = _infographic_index({'image_article': 2, 'infographic_article': 0}, image_index=0)
    assert index == 0, f'verwacht 0, kreeg {index}'
    print('  PASS test_main_handles_a_cached_image_that_ignores_the_selection')


def test_the_infographic_fallbacks_in_main_are_gone():
    """R5: select_articles_for_visuals levert altijd een geldige, andere index. Afvangen is dode code."""
    main_py = (Path(__file__).resolve().parent.parent / 'main.py').read_text()
    assert 'No infographic article selected' not in main_py, 'de tak voor een ontbrekende index staat er nog'
    assert '>= len(articles)' not in main_py, 'de klem voor een te grote index staat er nog'
    print('  PASS test_the_infographic_fallbacks_in_main_are_gone')


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
        test_body_falls_back_to_html_when_plaintext_has_no_links,
        test_body_prefers_plaintext_when_it_has_links,
        test_body_cap_keeps_the_news_not_just_the_ad_header,
        test_personal_tracking_links_are_recognized,
        test_classify_keeps_a_delivery_complaint_away_from_unsubscribing,
        test_classify_falls_back_to_hp_below_the_confidence_threshold,
        test_classify_accepts_confidence_exactly_on_the_threshold,
        test_classify_offers_all_four_categories,
        test_classify_truncates_a_long_body,
        test_classify_handles_an_empty_body,
        test_classify_lets_a_model_failure_through,
        test_visuals_takes_the_choice_of_jev,
        test_visuals_gives_the_infographic_the_runner_up_on_a_clash,
        test_visuals_breaks_a_tie_on_the_lowest_index,
        test_visuals_asks_two_choice_questions_over_all_articles,
        test_visuals_retries_once_on_a_failure,
        test_visuals_falls_back_after_two_failures,
        test_visuals_falls_back_on_a_malformed_answer,
        test_visuals_picks_two_valid_articles_from_three,
        test_subscriber_lookup_raises_on_a_database_failure,
        test_subscriber_lookup_returns_none_for_an_unknown_address,
        test_subscriber_lookup_still_returns_status_and_timestamp,
        test_replies_unsubscribes_a_known_address,
        test_replies_drops_an_unknown_unsubscribe_silently,
        test_replies_deletes_a_delay_without_counting_it,
        test_replies_counts_a_permanent_bounce,
        test_replies_does_not_count_a_bounce_it_could_not_delete,
        test_replies_leaves_a_bounce_without_a_recipient_alone,
        test_replies_skips_a_message_older_than_the_timestamp,
        test_replies_skips_its_own_mail,
        test_replies_survives_a_failing_classification,
        test_replies_survives_a_failing_imap_connection,
        test_replies_advances_the_timestamp_on_an_empty_label,
        test_replies_processes_everything_without_a_timestamp_file,
        test_replies_leaves_hp_mail_untouched,
        test_replies_keeps_a_log_line_on_one_line,
        test_replies_stamps_the_start_of_the_pass,
        test_the_inbox_bounce_route_is_gone,
        test_bounce_counting_is_unchanged,
        test_marking_undeliverable_respects_a_resubscribe,
        test_main_runs_the_handler_before_and_after_sending,
        test_main_skips_the_handler_on_a_dry_run,
        test_main_runs_the_first_pass_even_when_the_source_gate_stops_the_run,
        test_main_sends_the_newsletter_when_the_handler_fails,
        test_main_shifts_the_infographic_behind_the_moved_image_article,
        test_main_keeps_the_infographic_index_when_the_image_stays_first,
        test_main_handles_a_cached_image_that_ignores_the_selection,
        test_the_infographic_fallbacks_in_main_are_gone,
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
