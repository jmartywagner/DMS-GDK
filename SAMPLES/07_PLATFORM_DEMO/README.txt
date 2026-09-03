# 07_PLATFORM_DEMO

Exemple public DMS-1 construit autour d'un niveau 4096 x 224 qui traverse les cinq modes video officiels.

## Lancement

1. Lancer `DMS_GDK.bat`.
2. Selectionner `07_PLATFORM_DEMO`.
3. Utiliser **BUILD + RUN**.

Le build lit les ressources DIMG, DMAP et DCOLL du projet avant la compilation Motorola 68000.

## Controles

- Fleches gauche/droite : deplacement
- Z / bouton A : saut
- X / bouton B : invincibilite debug
- C / bouton C : musique DMR on/off
- Entree / START : restart checkpoint

## Parcours video

- M0 STANDARD : 320x224, BG A+B, 80 sprites / 20 par scanline
- M2 SCROLL : 320x224, BG A+B, line-scroll, 48 / 12
- M1 HIGH COLOR : 320x224, BG A, 8 palettes, 80 / 20
- M3 SPRITE : 320x224, BG A, 128 / 32
- M4 LOW RES : 256x224, BG A+B, 8 palettes, 96 / 24

Les sources C et les ressources editables accompagnees d'la cartouche `07_PLATFORM_DEMO_PREBUILT.dmc` preconstruite permettent d'etudier le pipeline complet.
