# Composer pour DMS-1 avec Furnace

Ce guide explique la configuration de base pour composer une musique DMS-1 dans Furnace puis la convertir vers le format DMR.

## 1. Construire la machine DMS-1 dans Furnace

DMS-1 utilise trois blocs sonores en même temps :

1. **YM2414 / OPZ** pour la synthèse FM
2. **AY-3-8910** pour les 3 canaux SSG
3. **YM2610** uniquement pour les samples ADPCM

Dans Furnace, créez donc un projet qui combine ces trois puces.

La musique DMS-1 est pensée comme une seule machine composée de ces trois blocs. Il ne faut pas travailler sur trois morceaux séparés.

## 2. Canaux réellement disponibles

### YM2414 / OPZ

DMS-1 utilise uniquement les quatre premiers canaux FM :

| Furnace | DMS-1 |
| --- | --- |
| OPZ CH1 | FM 1 |
| OPZ CH2 | FM 2 |
| OPZ CH3 | FM 3 |
| OPZ CH4 | FM 4 |
| OPZ CH5 | non utilisé |
| OPZ CH6 | non utilisé |
| OPZ CH7 | non utilisé |
| OPZ CH8 | non utilisé |

Les canaux OPZ 5 à 8 existent dans la puce, mais ils sont réservés dans la spécification DMS-1.

### AY-3-8910

Les trois canaux sont disponibles :

| Furnace | DMS-1 |
| --- | --- |
| AY A | SSG 1 |
| AY B | SSG 2 |
| AY C | SSG 3 |

### YM2610

Le YM2610 n'est pas utilisé comme synthétiseur FM ou SSG dans DMS-1.

Il sert uniquement aux samples :

| Furnace | DMS-1 |
| --- | --- |
| YM2610 ADPCM-A | samples ADPCM-A |
| YM2610 ADPCM-B | sample ADPCM-B |
| autres canaux YM2610 | non utilisés |

## 3. Nettoyer la vue tracker

Après avoir ajouté les trois puces, Furnace affiche davantage de canaux que le DMS-1 n'en utilise réellement.

Pour travailler proprement :

- gardez OPZ CH1 à CH4 ;
- gardez AY A, B et C ;
- gardez les canaux ADPCM-A et ADPCM-B du YM2610 ;
- retirez ou masquez de la vue de composition les canaux OPZ 5 à 8 ;
- retirez ou masquez les autres canaux YM2610 qui ne sont pas pris en compte par DMS-1.

L'objectif est que la vue tracker corresponde directement à la machine DMS-1 réelle.

## 4. Composer normalement

Une fois cette vue nettoyée, composez dans Furnace comme sur une machine unique :

- FM pour les sons synthétiques principaux ;
- SSG pour les lignes simples, arpèges, bruit et accents ;
- ADPCM-A pour les samples courts et percussifs ;
- ADPCM-B pour les samples plus longs.

Les quatre canaux FM actifs constituent une limite volontaire du DMS-1. Il est donc recommandé d'organiser les arrangements autour de cette contrainte plutôt que de préparer un morceau pour huit canaux OPZ puis de le réduire ensuite.

## 5. Exporter pour DMS-1

Lorsque le morceau est terminé, utilisez la fonction d'export de Furnace prévue pour récupérer les données du morceau destinées au convertisseur DMS.

**Ne faites pas un export VGM.**

Le VGM est un format de capture/lecture et ne correspond pas au pipeline de conversion DMS-1 vers DMR.

Le fichier exporté depuis Furnace doit ensuite être donné au convertisseur DMS Furnace -> DMR fourni dans :

`GDK/tools/DMS_FURNACE_DMR/`

Sous Windows, utilisez :

`DMS_FURNACE_DMR.bat`

Le convertisseur applique les limites de la machine DMS-1 et génère le fichier `.dmr` utilisé par le runtime.

## 6. Vérifier le morceau

Après conversion :

1. ouvrez le **DMS Music Player** ;
2. chargez le fichier `.dmr` ;
3. vérifiez le mix complet ;
4. écoutez séparément FM, SSG et samples si nécessaire ;
5. corrigez le morceau dans Furnace puis reconvertissez si besoin.

## 7. Intégrer la musique dans un projet

Une fois le DMR validé, placez-le dans les ressources du projet concerné et référencez-le dans le pipeline de build DMS.

Le projet `SAMPLES/07_PLATFORM_DEMO` sert de référence publique pour l'organisation générale d'un projet DMS-1.

## Résumé rapide

Pour composer pour DMS-1 dans Furnace :

1. combinez **YM2414/OPZ + AY-3-8910 + YM2610** ;
2. gardez seulement OPZ 1-4, AY A-B-C et les canaux ADPCM ;
3. masquez les autres canaux dans la vue tracker ;
4. composez avec les limites DMS-1 ;
5. faites l'export destiné au convertisseur ;
6. **n'utilisez pas l'export VGM** ;
7. convertissez avec `DMS_FURNACE_DMR.bat` ;
8. testez le `.dmr` dans le DMS Music Player.
