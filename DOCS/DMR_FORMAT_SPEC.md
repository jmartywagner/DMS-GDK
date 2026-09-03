# DMR 0.1 - Spécification du format musical DMS-1

DMR est le format musical exécutable de DMS-1. Il ne stocke pas une abstraction de haut niveau de type MIDI : il transporte une séquence temporelle d'écritures matérielles et de commandes ADPCM destinées au moteur audio DMS-1.

**Endianness : big-endian.**  
**Timebase : 24 000 000 ticks/s.**  
**Taille maximale du fichier : 16 MiB.**

## 1. Header, 64 octets

| Offset | Taille | Champ | Valeur / règle |
|---:|---:|---|---|
| `0x00` | 4 | magic | ASCII `DMR0` |
| `0x04` | 2 | major | `0` |
| `0x06` | 2 | minor | `1` |
| `0x08` | 2 | header_size | `64` |
| `0x0A` | 2 | réservé | `0` |
| `0x0C` | 4 | total_size | taille exacte du fichier |
| `0x10` | 4 | hardware_id | ASCII `DMS1` |
| `0x14` | 4 | clock_profile | `1` = profil NATIVE89 |
| `0x18` | 4 | directory_offset | offset du répertoire de chunks |
| `0x1C` | 2 | directory_count | nombre de chunks |
| `0x1E` | 2 | directory_entry_size | `16` |
| `0x20` | 4 | entrypoint | adresse absolue de la première instruction DSEQ, dans `CODE` |
| `0x24` | 4 | timebase | `24000000` |
| `0x28` | 24 | réservé | tous les octets doivent être nuls |

## 2. Répertoire de chunks

Chaque entrée fait 16 octets :

| Offset relatif | Taille | Champ |
|---:|---:|---|
| `+0x00` | 4 | type ASCII sur 4 caractères |
| `+0x04` | 4 | offset absolu |
| `+0x08` | 4 | taille en octets |
| `+0x0C` | 4 | flags |

Deux chunks du même type sont interdits.

Chunks actuellement définis :

- `CODE` : obligatoire, programme DSEQ.
- `META` : métadonnées texte `clé=valeur`.
- `SDIR` : répertoire des samples ADPCM.
- `SAMP` : données ADPCM paginées.

`SDIR` et `SAMP` doivent être présents ensemble ou absents ensemble.

Le recorder de référence écrit `CODE + META`, puis ajoute `SDIR + SAMP` lorsqu'il existe des samples.

## 3. CODE / DSEQ

DSEQ est un petit bytecode déterministe. Le compteur temporel est exprimé en cycles de la timebase 24 MHz.

### Opcodes

| Opcode | Nom | Données | Effet |
|---:|---|---|---|
| `0x00` | HALT | - | termine la séquence |
| `0x01` | WAIT | ULEB128 cycles | avance le temps |
| `0x10` | WR8 | `u16 address`, `u8 data` | écrit un octet sur le bus MMIO audio |
| `0x11` | WRN | `u16 address`, `u8 length`, `length × u8` | écrit plusieurs octets consécutifs |
| `0x20` | PLAY_A | `u16 sample_id`, `u8 level`, `u8 pan` | lance un sample ADPCM-A |
| `0x21` | STOP_A | - | arrête ADPCM-A |
| `0x22` | PLAY_B | `u16 sample_id`, `u16 delta_n`, `u8 level`, `u8 pan`, `u8 flags` | lance ADPCM-B |
| `0x23` | STOP_B | - | arrête ADPCM-B |
| `0x30` | JUMP | `u32 target` | saut absolu dans `CODE` |
| `0x31` | LOOP | `u8 slot`, `u16 count`, `u32 target` | boucle comptée |

`WAIT 0` n'avance pas le temps. Une séquence qui boucle indéfiniment sans progression temporelle est rejetée par le runtime.

Les cibles `JUMP` et `LOOP` doivent rester dans le chunk `CODE`.

### ULEB128

Les durées de `WAIT` utilisent ULEB128. Exemple conceptuel : une durée courte tient dans un octet ; les durées plus longues utilisent plusieurs octets avec le bit de continuation.

Le temps en secondes vaut :

`cycles / 24 000 000`.

## 4. Bus audio vu par WR8 / WRN

| Adresse | Destination |
|---|---|
| `0x0000–0x00FF` | OPZ |
| `0x0100–0x010F` | SSG |
| `0x0120–0x012F` | ADPCM-A |
| `0x0140–0x015F` | ADPCM-B |
| `0x0180–0x019F` | mixer / système |

Le format autorise des écritures de registre, mais la machine DMS-1 impose ses propres limites. En particulier, les écritures de registres OPZ visant les canaux CH5–CH8 sont ignorées : seules FM1–FM4 font partie de DMS-1.

## 5. META

`META` contient du texte UTF-8/ASCII compatible, une paire `clé=valeur` par ligne.

Le recorder courant produit notamment :

- `title`
- `author`
- `compiler`
- `timing`
- `capture_cycles`
- `event_count`
- `adpcm_b_nominal_rate`

Un lecteur ne doit pas dépendre de l'ordre de ces lignes pour exécuter `CODE`.

## 6. SDIR - répertoire samples

Chaque entrée fait exactement 16 octets :

| Offset relatif | Taille | Champ | Règle |
|---:|---:|---|---|
| `+0x00` | 2 | sample_id | identifiant non nul et unique dans les fichiers produits par le recorder |
| `+0x02` | 1 | codec | `1` = ADPCM-A, `2` = ADPCM-B |
| `+0x03` | 1 | flags | réservé dans le recorder 0.1 |
| `+0x04` | 2 | start_page | page de 256 octets |
| `+0x06` | 2 | end_page | page de 256 octets, inclusive |
| `+0x08` | 4 | source_rate | fréquence source déclarée |
| `+0x0C` | 1 | level | niveau par défaut |
| `+0x0D` | 1 | pan | routage par défaut |
| `+0x0E` | 1 | root_note | note racine |
| `+0x0F` | 1 | fine_cents | `int8` signé |

`SDIR.size` doit être un multiple de 16.

## 7. SAMP - données samples

- offset de `SAMP` aligné sur 256 octets ;
- taille de `SAMP` multiple de 256 octets ;
- `start_page` et `end_page` de chaque entrée SDIR pointent à l'intérieur du chunk ;
- les données d'un sample produit par le recorder sont elles-mêmes alignées à 256 octets.

## 8. Panoramique ADPCM et SSG

Pour les champs/routages stéréo courants :

- `0x80` : gauche ;
- `0x40` : droite ;
- `0xC0` : gauche + droite.

ADPCM-A/B utilisent leur propre panoramique.

Le runtime audio V0.8 conserve `0x018D` comme routage global compatible avec les anciens DMR et expose aussi un panoramique par canal : `0x018E` pour SSG A, `0x018F` pour SSG B et `0x0190` pour SSG C.

## 9. Mixer système

| Adresse | Fonction |
|---|---|
| `0x0188` | gain FM |
| `0x0189` | gain SSG |
| `0x018A` | gain ADPCM-A |
| `0x018B` | gain ADPCM-B |
| `0x018C` | gain master |
| `0x018D` | route SSG globale, compatibilite historique |
| `0x018E` | panoramique SSG A |
| `0x018F` | panoramique SSG B |
| `0x0190` | panoramique SSG C |

Pour les gains, bit 7 = mute et les 6 bits faibles codent une atténuation par pas de 0,75 dB.

## 10. Compatibilité

DMR **0.1** est une version de contrat. Un changement incompatible du header, du sens d'un opcode, de la structure SDIR ou des règles d'exécution doit entraîner une nouvelle version de format.

Les convertisseurs Furnace/Ableton ou tout autre outil externe doivent produire le même contrat binaire ; ils ne doivent pas contourner les limites de la machine.
