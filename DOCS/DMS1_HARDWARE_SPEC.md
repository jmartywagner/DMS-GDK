# DMS-1 - specification materielle publique

Etat public de reference : 3 septembre 2026.

DMS-1 est une console 16-bit fictive concue comme une machine 1989-1991 coherente, avec des limites materielles explicites et un GDK dedie.

## Horloges

| Element | Valeur |
|---|---:|
| Horloge maitre | 24.000000 MHz |
| Motorola 68000 | 10.000000 MHz |
| Zilog Z80 | 4.000000 MHz |
| Video | 60 Hz |
| OPZ / YM2414 derive | 3.579545 MHz |
| SSG | 2 MHz |
| ADPCM-A | ~18.5185 kHz |
| ADPCM-B, cible musicale nominale | ~26 kHz |
| Sortie host de reference | 44.1 kHz, stereo, 16 bits |

## Memoire 68000

| Adresse | Taille | Fonction |
|---|---:|---|
| `0x000000` | cartouche | ROM M68K, vecteurs et code |
| `0x100000` | 64 KiB | Work RAM |
| `0x200000` | 128 KiB | VRAM |
| `0x220000` | 256 B | CRAM, 128 entrees RGB333 |
| `0x300000` | 256 B | registres VDP |
| `0x400000` | 256 B | PAD / I/O |
| `0x500000` | 256 B | mailbox 68000/Z80 |

## Video

Espace couleur maitre : **RGB333, 512 couleurs**. Tiles et sprites utilisent **4 bpp**, soit 16 indices par palette.

| Mode | Resolution | Palettes | Plans | Sprites | Par scanline |
|---|---:|---:|---|---:|---:|
| M0 STANDARD | 320x224 | 4 | BG A + BG B | 80 | 20 |
| M1 HIGH COLOR | 320x224 | 8 | BG A | 80 | 20 |
| M2 SCROLL | 320x224 | 4 | BG A + BG B, line-scroll | 48 | 12 |
| M3 SPRITE | 320x224 | 4 | BG A | 128 | 32 |
| M4 LOW RES | 256x224 | 8 | BG A + BG B | 96 | 24 |

Le changement de mode s'effectue pendant le VBlank.

## Audio

- OPZ / YM2414 derive : 4 canaux FM DMS actifs, canaux 5 a 8 reserves.
- SSG : 3 canaux.
- ADPCM-A : samples courts, avec 3 voix de mix simultanees dans le runtime audio V0.8.
- ADPCM-B : samples longs, lecture Delta-N.

### MMIO audio

| Plage / registre | Fonction |
|---|---|
| `0x0000-0x00FF` | OPZ |
| `0x0100-0x010F` | SSG |
| `0x0120-0x012F` | ADPCM-A |
| `0x0140-0x015F` | ADPCM-B |
| `0x0188` | gain FM |
| `0x0189` | gain SSG |
| `0x018A` | gain ADPCM-A |
| `0x018B` | gain ADPCM-B |
| `0x018C` | gain master |
| `0x018D` | routage SSG global, compatibilite DMR historique |
| `0x018E` | panoramique SSG A |
| `0x018F` | panoramique SSG B |
| `0x0190` | panoramique SSG C |
| `0x0191` | selection de voix ADPCM-A |

Le runtime V0.8 conserve le routage global historique et ajoute le panoramique individuel des trois canaux SSG.

## Reference d'execution

Le contrat public repose sur la specification materielle, les formats binaires, le runtime de reference et les tests de contrat du GDK.
