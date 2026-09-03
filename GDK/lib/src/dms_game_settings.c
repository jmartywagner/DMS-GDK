#include "dms_game_settings.h"

/* Valeurs de secours pour les projets sans dms_game_settings.json. Le symbole
 * généré est fort et remplace automatiquement cette définition faible. */
__attribute__((weak)) const DmsGameSettings dms_game_settings = {
    768, 512, 192, 1, 96, -1536, -1408, -896, 12, 2048,
    0, -16, 56, 36, 1024, 0, 0, 4096, 2048,
    256, 256, 64, 32, 1, 3, 2, 600, 30, 360,
    1, 352, 42, -256, 1, 1, 520, 78, -128, 2, -20, 1
};
