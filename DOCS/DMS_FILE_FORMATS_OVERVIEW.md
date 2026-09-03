# Formats de fichiers DMS - vue d'ensemble

Ce document donne la carte des formats actuellement consommés par le compilateur de ressources. Le format musical DMR est documenté séparément et plus précisément dans `DMR_FORMAT_SPEC.md`.

## Contrats principaux

| Extension / famille | Version courante | Nature | Rôle |
|---|---:|---|---|
| DRES | V3 | ZIP structuré | sprites / frames / tiles 4 bpp et palettes |
| DIMG | V2 | ZIP structuré | image/tileset, palettes et tilemap |
| DMAP | V2 | ZIP structuré | map BG A/B, priorités et métadonnées |
| DCOLL | V1 | ZIP structuré | zones et polygones de collision + actions |
| DACTOR | V1 | ZIP structuré | définition d'acteur et états |
| DSCENE | V2 | JSON | scène éditable et liens vers ressources |
| DFLOW | version courante de l'outil | JSON | graphe de progression / flow du jeu |
| DMR | 0.1 | binaire big-endian | musique/séquence matérielle audio DMS-1 |
| DMC | interne cartouche | conteneur binaire | image exécutable de jeu produite par le GDK |

## Fichiers obligatoires des conteneurs ZIP

### DRES V3

- `manifest.json`
- `tiles.bin`
- `palettes.bin`

### DIMG V2

- `manifest.json`
- `tiles.bin`
- `palettes.bin`
- `palette_ids.bin`
- `tilemap.bin`

### DMAP V2

- `manifest.json`
- `bg_a.bin`
- `bg_b.bin`
- `priority_a.bin`
- `priority_b.bin`

### DCOLL V1

- `manifest.json`
- `zones.bin`
- `vertices.bin`
- `actions.json`

### DACTOR V1

- `manifest.json`
- `actor.json`

Le `manifest.json` de chaque conteneur porte au minimum son `format` et son `format_version`. Le compilateur refuse un format ou une version qu'il ne connaît pas.

## Rôle du compilateur `dmsres`

`GDK/tools/dmsres.py` ne remplace pas les formats sources. Il les valide, vérifie leur cohérence avec le mode vidéo et les autres ressources du projet, puis fabrique des produits internes de compilation (`resources.bin` et métadonnées C générées).

Exemple de manifeste projet :

```text
SPRITE PLAYER res/player.dres PALETTE_BASE=2
IMAGE TILESET res/stage_tiles.dimg
MAP STAGE01 res/stage01.dmap TILESET=TILESET
COLLISION STAGE01_COLL res/stage01.dcoll MAP=STAGE01
ACTOR HERO res/hero.dactor SPRITE=PLAYER COLLISION=STAGE01_COLL
MUSIC LEVEL1 res/level1.dmr
AUDIO GAME_AUDIO res/audio
```

Les formats graphiques restent soumis aux règles matérielles : RGB333, 4 bpp, palettes, plans et budgets du mode DMS-1 choisi.

## Etat de la documentation

DMR dispose d'un contrat detaille. Les autres formats sont exposes ici par leur structure publique et restent valides par `dmsres` lors du build.
