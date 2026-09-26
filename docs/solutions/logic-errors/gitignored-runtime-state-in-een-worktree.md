---
title: Een destructieve verificatiestap geeft in een worktree een andere uitkomst dan op main
category: logic-errors
date: 2026-09-26
component: verification
tags: [worktree, gitignore, runtime-state, verification, destructive-step, bounce-threshold]
commit: db0128f
plan: docs/plans/2026-09-26-001-feature-nieuwsbrief-replies-afhandelen-plan.md
---

# Een destructieve verificatiestap geeft in een worktree een andere uitkomst dan op main

## Symptoom

Niet gebeurd, net voorkomen. Het plan voor de replies-afhandelaar had als verificatiepoort
`uv run python -m src.replies`: één handmatige pass over het echte Gmail-label, te draaien in
de worktree waarin gebouwd werd. Die pass telt een harde bounce mee in `data/undelivered.json`
en zet een abonnee op `undeliverable` zodra de drempel geraakt wordt.

In de worktree zou hij `jeroen@nas.nl` níet op `undeliverable` hebben gezet. Op main wél. Zelfde
code, zelfde mail, andere uitkomst.

## Root cause

`data/undelivered.json` staat in `.gitignore`. Een `git worktree add` geeft een verse checkout
van de branch, en gitignored bestanden reizen niet mee. In de worktree bestond het bestand dus
niet.

De bouncedrempels in `src/undelivered.py` rekenen met de opgebouwde historie:

```python
threshold = PERMANENT_BOUNCE_THRESHOLD if entry['permanent_count'] > 0 else TEMPORARY_BOUNCE_THRESHOLD
if entry['count'] >= threshold:
    emails_to_mark_undeliverable.append(recipient_email)
```

Op main stond `jeroen@nas.nl` al op `count: 16, permanent_count: 0`. De 5xx-bounce maakte daar
`count: 17, permanent_count: 1`, de drempel zakte naar 2, en 17 >= 2 is waar: status naar
`undeliverable`. In de worktree zou dezelfde bounce tegen een leeg bestand geteld zijn:
`count: 1, permanent_count: 1`, en 1 >= 2 is onwaar. Geen statuswijziging.

Het patroon: **een verificatiestap die accumulerende runtime-state muteert is niet
locatie-onafhankelijk. Draai je hem op de verkeerde plek, dan bewijst hij iets anders dan je
denkt, en hij bewijst het stil.**

De valstrik zit in de combinatie. Worktree-isolatie is er juist om de hoofdcheckout te
beschermen, en dat werkt uitstekend voor code. Voor state die bewust buiten git valt draait het
om: de isolatie maakt de omgeving onvolledig in plaats van veilig.

## Wat te doen

- Zet een destructieve verificatiestap die accumulerende runtime-state aanraakt **na de merge,
  vanuit de hoofdcheckout**. Zet dat expliciet in het plan, niet alleen de commandoregel.
- Vraag bij elke poort die `data/` schrijft: leest deze code historie die alleen op main
  bestaat? Check `.gitignore` tegen de bestanden die de stap aanraakt.
- Maak een kopie van het state-bestand voordat de stap draait. Er is geen andere weg terug:
  `data/undelivered.json` staat niet in git, dus `git checkout` haalt hem niet op.
- Draai de stap daarna een tweede keer. Een idempotente tweede pass (hier: `0 van 12 berichten
  afgehandeld`) bewijst dat het tijdstempelfilter werkt en dat er niet dubbel geteld wordt.

## Wat dit niet is

Geen argument tegen worktrees. De code-isolatie deed precies wat ze moet doen, en de 55 tests
draaiden er prima, want die gebruiken allemaal een `tempfile.TemporaryDirectory()` in plaats van
`data/`. Het gaat specifiek om de handmatige stap die het echte bestand aanraakt.
