libdms - GDK P1.2.10
====================

`libdms` fournit l API C cible Motorola 68000 utilisee par les projets DMS-1.
Les headers publics se trouvent dans `GDK/include` et les implementations dans `GDK/lib/src`.

Le pipeline GCC compile le code jeu et libdms avec la toolchain locale `m68k-elf`. Les fonctions exposees restent alignees sur les limites materielles DMS-1 et sur le runtime de reference.
