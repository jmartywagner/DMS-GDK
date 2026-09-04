# DMS-1 / DMS-GDK

DMS-1 est une console 16-bit fictive concue comme une machine retro 1989-1991 coherente. Le projet associe une architecture materielle definie, un runtime de reference, un GDK Motorola 68000 et des outils de creation graphiques, gameplay et audio.

Cette **Public V1** constitue la base technique partageable du projet DAC MASTER.

## Contenu

- `GDK/` : headers, libdms, linker, compilateurs de ressources et pipeline GCC 68000.
- `RUNTIME/` : coeur 68000, VDP, audio et executables Windows de reference.
- `TOOLS/` : Asset Lab, Map Builder, Collision Builder, Actor Builder, Image Converter, Scene Builder, Game Flow Builder, Audio Asset Builder, Metadata Editor et Music Player.
- `TEMPLATES/STARTER_GAME/` : squelette de projet.
- `SAMPLES/07_PLATFORM_DEMO/` : exemple complet utilisant les cinq modes video DMS-1.
- `DOCS/` : specification materielle et formats publics.
- `ADMIN/` : diagnostic, installation de la toolchain et maintenance locale.

## Demarrage Windows

Prerequis : Windows 10/11, Python 3 et Pillow.

1. Installer Python et lancer `pip install -r requirements.txt`.
2. Lancer `ADMIN\REPAIR_TOOLCHAIN_68000.bat` pour installer la toolchain locale dans `TOOLCHAIN\m68k-elf`.
3. Lancer `ADMIN\DMS_DOCTOR.bat` pour verifier l'environnement.
4. Lancer `DMS_GDK.bat`.
5. Ouvrir `07_PLATFORM_DEMO`, puis utiliser **BUILD + RUN**.

Le depot garde la toolchain croisee en installation locale afin de rester leger.

## DMS-1 en bref

- Motorola 68000 a 10 MHz
- Z80 a 4 MHz
- 64 KiB de RAM principale
- RGB333, 512 couleurs maitre
- tiles et sprites 4 bpp
- cinq modes video 320x224 / 256x224
- OPZ/YM2414 derive : 4 canaux FM actifs
- SSG : 3 canaux
- ADPCM-A et ADPCM-B
- sortie de reference : 44.1 kHz stereo 16 bits

La specification complete se trouve dans `DOCS/DMS1_HARDWARE_SPEC.md`.

## Composer de la musique avec Furnace

Le pipeline public de composition utilise **Furnace** avec trois puces combinees dans le meme projet : **YM2414/OPZ + AY-3-8910 + YM2610**.

DMS-1 utilise OPZ CH1 a CH4, les trois canaux AY et les canaux ADPCM du YM2610. Les autres canaux doivent etre masques ou retires de la vue tracker pour que l'espace de composition corresponde a la machine.

L'export destine au convertisseur DMR **n'est pas un export VGM**.

Guide complet : [DOCS/FURNACE_QUICKSTART.md](DOCS/FURNACE_QUICKSTART.md)

## Exemple public

`SAMPLES/07_PLATFORM_DEMO` traverse les cinq modes video officiels dans un niveau unique. Le dossier contient les sources C, les ressources editables utiles, les ressources compilees et une cartouche `.dmc` preconstruite.

## Versions de cette release

- DMS-GDK : P1.2.10
- Runtime principal : P1.0.9 Final Runtime Lock
- Audio Core : V0.8.6
- Music Player : V0.4.10
- Release publique : V1.0.0, 4 septembre 2026

## Licence

Le code et les assets DMS-1 / DMS-GDK sont proposes sous la licence du projet decrite dans `LICENSE.md`. Musashi et ymfm conservent leurs licences respectives, reproduites dans `LICENSES/` et resumees dans `THIRD_PARTY_NOTICES.md`.

## Projet

DMS-1 fait partie de **DAC MASTER**, un projet de recherche, creation et developpement autour des architectures sonores et graphiques 8/16-bit.

https://www.studio-dmp-radiohouse.com/dac-master/dms-1
