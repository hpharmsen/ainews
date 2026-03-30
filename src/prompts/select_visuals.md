Ik heb een nieuwsbrief over AI met de volgende {num_articles} artikelen:
<nieuwsbrief>
{articles}
</nieuwsbrief>

Ik wil twee visuals genereren voor deze nieuwsbrief:
1. Een artistieke ILLUSTRATIE - geen generiek AI beeld met robots en futuristische lijnen, maar een illustratie die echt past bij de inhoud van één van de artikelen.
2. Een serieuze INFOGRAPHIC - met concrete data en feiten uit één van de artikelen.

BELANGRIJK: Kies twee VERSCHILLENDE artikelen. De illustratie en infographic mogen NIET over hetzelfde artikel gaan.

Doe de volgende stappen:
1. Analyseer welke artikelen zich het beste lenen voor een artistieke illustratie
2. Analyseer welke artikelen zich het beste lenen voor een infographic met data/feiten
3. Selecteer twee verschillende artikelen en beschrijf wat er in elke visual moet komen

Geef je antwoord in JSON met deze velden:
- image_article: index van het artikel voor de illustratie (0 tot {max_index})
- image_description: beschrijving van de illustratie
- infographic_article: index van het artikel voor de infographic (0 tot {max_index}, MOET ANDERS ZIJN dan image_article)
- infographic_description: beschrijving van de infographic