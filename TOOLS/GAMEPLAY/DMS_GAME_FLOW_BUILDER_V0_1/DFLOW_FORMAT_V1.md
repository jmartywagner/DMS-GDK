# DFLOW V1 - spécification compacte

`*.dflow` est un document JSON UTF-8.

En-tête obligatoire :

```json
{
  "format": "DFLOW",
  "format_version": 1,
  "name": "MY GAME",
  "main_flow": "MAIN",
  "flows": [],
  "nodes": [],
  "transitions": []
}
```

## flows

Chaque flow possède `id`, `name`, `entry_state`.

`MAIN` est le flow principal par convention. Les flows enfants sont référencés par les nœuds `SUBFLOW` via `subflow_id`.

## nodes

Champs principaux :

- `id` : identifiant C stable ;
- `name` : nom utilisateur ;
- `type` : SCREEN / MENU / GAME / CUTSCENE / SUBFLOW ;
- `flow_id` : flow parent ;
- `x`, `y` : position éditeur ;
- `video_mode` : -1 ou 0..4 ;
- `scene`, `map`, `collision`, `actor`, `music`, `image`, `sprite`, `audio` : références facultatives ;
- `enter_fx`, `exit_fx` : NONE / FADE_IN / FADE_OUT / FLASH / SHAKE ;
- `enter_callback`, `update_callback`, `exit_callback` : symboles C facultatifs ;
- `subflow_id` : uniquement pour SUBFLOW.

Le format conserve `scene` pour compatibilité future, mais aucun Scene Builder / format DSCENE officiel n'a été trouvé dans l'archive DMS auditée le 12 août 2026. Une référence `.dscene` n'est donc acceptée que si le fichier existe réellement.

## transitions

Champs :

- `id`, `name` ;
- `source`, `destination` ;
- `event` (`AUTO` = ID 0) ;
- `condition` : callback `uint8_t condition(void)` facultatif ;
- `delay_frames` ;
- `visual_fx`, `fx_duration` ;
- `priority` : nombre faible = priorité forte.

## Export binaire

`game_flow_data.bin` commence par `DFLW`, version big-endian, nombre d'états, nombre de transitions et ID d'entrée. Il contient ensuite des enregistrements compacts d'états et de transitions. Le runtime V0.1 utilise les tables C générées ; le BIN est conservé comme représentation GDK stable pour les futurs loaders/outils.
