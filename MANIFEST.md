# DMS-1 / DMS-GDK — Public V1 Manifest

Release publique : **V1.0.0**  
Date : **4 septembre 2026**  
Projet : **DAC MASTER / DMS-1**  
Auteur : **Jonathan Marty-Wagner / DMP Radiohouse**

Ce document fige le perimetre officiel de la premiere release publique de DMS-1 / DMS-GDK.

## Versions verrouillees

- DMS-GDK : **P1.2.10**
- Runtime principal : **P1.0.9 Final Runtime Lock**
- Audio Core : **V0.8.6**
- Music Player : **V0.4.10**
- Release publique : **V1.0.0**

## Perimetre public

### GDK

`GDK/`

Base de developpement canonique pour DMS-1 : headers, bibliotheque `libdms`, linker, compilation des ressources et pipeline GCC Motorola 68000.

### Runtime

`RUNTIME/`

Runtime de reference DMS-1 incluant le coeur Motorola 68000, le VDP, l'audio et les executables Windows necessaires au lancement et aux tests.

### Outils

`TOOLS/`

Outils retenus dans la Public V1 :

- Asset Lab
- Map Builder
- Collision Builder
- Actor Builder
- Image Converter
- Scene Builder
- Game Flow Builder
- Audio Asset Builder
- Metadata Editor
- Music Player

La presence d'un outil dans cette release signifie qu'il appartient au perimetre public V1. Les fonctions experimentales ou non stabilisees qui ne font pas partie de ce depot restent hors release.

### Projet de depart

`TEMPLATES/STARTER_GAME/`

Squelette de projet destine a servir de base a un nouveau projet DMS-1.

### Exemple de reference

`SAMPLES/07_PLATFORM_DEMO/`

Exemple public principal de la V1. Il contient les sources C, les ressources utiles au projet, les ressources compilees et une cartouche `.dmc` de reference. Il constitue le projet de validation principal du pipeline public.

### Documentation

`DOCS/`

Documentation publique de DMS-1, notamment la specification materielle, les formats et le guide de composition Furnace / DMR.

### Administration et diagnostic

`ADMIN/`

Outils locaux de diagnostic, installation/reparation de la toolchain et maintenance du GDK.

## Dependances et installation

Environnement de reference :

- Windows 10 / 11
- Python 3
- Pillow
- toolchain croisee Motorola 68000 installee localement par le pipeline DMS-GDK

Le processus d'installation et de validation est decrit dans `README.md`.

## Validation de la release

La Public V1 est consideree valide lorsque le depot permet, depuis une installation propre :

1. l'installation des dependances Python ;
2. l'installation ou la reparation de la toolchain 68000 ;
3. le passage du diagnostic DMS Doctor ;
4. le lancement du GDK ;
5. la compilation et l'execution de `07_PLATFORM_DEMO`.

Ce parcours a ete utilise comme parcours reel d'installation et de test avant publication.

## Hors perimetre V1

Restent volontairement hors de cette release :

- anciennes branches et anciennes copies du GDK ;
- prototypes abandonnes ;
- builds intermediaires ;
- outils ou fonctions experimentales non retenus pour la Public V1 ;
- projets de jeux en cours de production ;
- fichiers de travail personnels ;
- archives de developpement et sauvegardes locales.

La Public V1 constitue une base technique figee et partageable. Les evolutions futures devront etre versionnees separement.

## Licence et composants tiers

Le code, les outils, la documentation et les assets propres a DMS-1 / DMS-GDK suivent `LICENSE.md`.

Les composants tiers conservent leurs licences respectives. Voir :

- `THIRD_PARTY_NOTICES.md`
- `LICENSES/`

## Reference publique

DMS-1 fait partie du projet DAC MASTER :

https://www.studio-dmp-radiohouse.com/dac-master/dms-1
