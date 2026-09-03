from __future__ import annotations

import json
import os
import re
import struct
import zipfile
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "DMS Actor Builder"
APP_VERSION = "1.1.0 BIG UX BUILD"

PROFILS = [
    "Personnage plateforme", "Ennemi plateforme", "Joueur shoot'em up", "Ennemi shoot'em up",
    "Joueur beat'em up", "Ennemi beat'em up", "Exploration / aventure", "PNJ / dialogue",
    "Joueur RPG", "Ennemi RPG", "Objet puzzle", "Projectile", "Boss", "Personnalisé"
]
TYPES = ["JOUEUR", "ENNEMI", "BOSS", "PNJ", "PROJECTILE", "OBJET", "OBJET_INTERACTIF", "PLATEFORME_MOBILE", "EFFET", "PERSONNALISE"]
AXES = ["AUCUN", "HORIZONTAL", "VERTICAL", "LIBRE_2D", "4_DIRECTIONS", "8_DIRECTIONS"]
IA = ["AUCUNE", "PATROUILLE", "POURSUITE", "SHMUP_LIGNE", "BEAT_EM_UP", "NPC", "RPG", "BOSS_PHASES"]
GROUPES = ["NEUTRE", "JOUEUR", "ENNEMI", "BOSS", "PROJECTILE_JOUEUR", "PROJECTILE_ENNEMI", "OBJET", "PNJ", "ENVIRONNEMENT", "TOUS", "PERSONNALISE"]
CONDITIONS = ["TOUJOURS", "ENTREE_JOUEUR", "BOUTON_PRESSE", "BOUTON_TENU", "BOUTON_RELACHE", "AU_SOL", "PAS_AU_SOL", "TOUCHE_MUR", "TOUCHE_PLAFOND", "VITESSE_X", "VITESSE_Y", "TEMPS_ETAT", "ANIMATION_TERMINEE", "PV_INFERIEUR_OU_EGAL", "PV_SUPERIEUR", "JOUEUR_DISTANCE_X", "JOUEUR_DISTANCE_Y", "JOUEUR_DISTANCE"]
RUNTIME_ACTIONS = ["AUCUNE", "SAUTER"]
ACTION_CATEGORIES = ["MOUVEMENT", "ATTAQUE", "INTERACTION", "SPECIAL", "SYSTEME", "PERSONNALISE"]
ACTION_TRIGGERS = ["BOUTON_PRESSE", "BOUTON_TENU", "BOUTON_RELACHE", "METADONNEE_SEULE"]
CAPTEURS = ["SOL_DEVANT", "MUR_DEVANT", "PLAFOND", "JOUEUR_PROCHE", "JOUEUR_VISIBLE", "DANGER_DEVANT", "BORD_ECRAN", "ZONE_DECLENCHEUR", "ACTEUR_PROCHE", "PERSONNALISE"]
DIRECTIONS = ["FACE", "GAUCHE", "DROITE", "HAUT", "BAS", "VERS_JOUEUR", "ANGLE_FIXE", "8_DIRECTIONS", "PERSONNALISE"]
FORMES_ATTAQUE = ["DRES_HITBOX", "RECTANGLE", "CERCLE", "POINT", "AUCUNE"]
TYPES_VAR = ["ENTIER", "DECIMAL", "BOOLEEN", "TEXTE"]
PAD_BUTTONS = ["A", "B", "+", "×"]

BUTTON_BITS = {
    "UP": 0x01, "HAUT": 0x01,
    "DOWN": 0x02, "BAS": 0x02,
    "LEFT": 0x04, "GAUCHE": 0x04,
    "RIGHT": 0x08, "DROITE": 0x08,
    "A": 0x10, "B": 0x20,
    "C": 0x40, "PLUS": 0x40, "+": 0x40,
    "START": 0x80, "X": 0x80, "MULT": 0x80, "MULTIPLY": 0x80, "FOIS": 0x80, "×": 0x80,
}

HELP_FIELDS = {
    "profil": "Profil de départ : préremplit un acteur typique. Ce n'est pas un verrou : tous les réglages restent modifiables ensuite.",
    "type": "Catégorie logique de l'acteur. Elle aide le diagnostic et les outils ; le comportement réel vient surtout du mouvement, des états, transitions, attaques et de l'IA.",
    "dres": "Ressource sprite produite par Asset Lab. Elle contient les animations, frames, palettes et boîtes disponibles pour l'acteur.",
    "dcoll": "Collision du monde. Pour un personnage, le corps par frame vient du DRES ; le DCOLL décrit surtout le niveau, ses sols, murs et zones.",
    "axe": "Mode de locomotion runtime. HORIZONTAL = plateforme classique ; VERTICAL = déplacement haut/bas ; LIBRE_2D = X+Y ; 4_DIRECTIONS = X ou Y ; 8_DIRECTIONS = diagonales autorisées.",
    "vitesse_max_x": "Vitesse horizontale maximale en pixels par frame environ. Plus la valeur est grande, plus l'acteur se déplace vite.",
    "vitesse_max_y": "Vitesse verticale pilotée pour les jeux top-down, SHMUP ou déplacement libre. La vitesse de chute possède son propre réglage.",
    "acceleration_x": "Vitesse à laquelle le déplacement horizontal atteint sa vitesse cible.",
    "acceleration_y": "Vitesse à laquelle le déplacement vertical atteint sa vitesse cible.",
    "freinage_x": "Décélération horizontale quand aucune direction X n'est tenue.",
    "freinage_y": "Décélération verticale quand aucune direction Y n'est tenue.",
    "gravite": "Accélération verticale automatique. Mets 0 pour SHMUP/top-down. L'état courant peut aussi désactiver la gravité.",
    "vitesse_chute_max": "Limite de vitesse de chute sous l'effet de la gravité.",
    "vitesse_saut": "Impulsion verticale appliquée par l'action runtime SAUTER. Une valeur négative fait monter l'acteur.",
    "nombre_sauts": "Nombre maximal de sauts avant de retoucher le sol : 1 = saut simple, 2 = double saut, etc.",
    "coyote_ms": "Tolérance après avoir quitté une plateforme pendant laquelle un saut reste accepté.",
    "buffer_saut_ms": "Tolérance avant l'atterrissage : une pression de saut légèrement trop tôt est mémorisée.",
    "masse": "Métadonnée de gameplay pour les systèmes de poussée/poids. Le runtime physique de base ne simule pas une vraie masse newtonienne.",
    "peut_tomber": "Indique qu'un acteur peut être soumis aux chutes/vides. Utile à l'auteur et aux futurs comportements spécialisés.",
    "traverse_plateformes": "Autorise conceptuellement la traversée de plateformes à sens unique. À utiliser avec le système de collision du projet qui les gère.",
    "pousser_objets": "Autorise conceptuellement la poussée d'objets. Le comportement précis dépend des règles du jeu.",
    "actif_hors_ecran": "Si activé, l'acteur continue d'être mis à jour hors écran dans la marge prévue.",
    "detruire_hors_ecran": "Détruit l'acteur lorsqu'il sort de la zone active : pratique pour projectiles et ennemis SHMUP.",
    "marge_activation": "Marge en pixels autour de l'écran avant activation/désactivation hors écran.",
    "respawn": "Si activé, une zone DANGER peut replacer l'acteur à son point de spawn au lieu de le laisser continuer.",
    "respawn_ms": "Délai d'intention pour le respawn. Conservé comme donnée d'auteur même si le runtime actuel respawn immédiatement sur DANGER.",
    "joueur": "Active les entrées manette sur cet acteur.",
    "diagonales": "Autorise X et Y en même temps. Désactivé, un profil 4 directions évite les diagonales ; activé, un profil 8 directions les accepte.",
    "bouton_action": "Raccourci de compatibilité pour l'action générique. Pour les nouveaux acteurs, préfère la liste Actions configurable juste en dessous.",
    "bouton_attaque": "Raccourci de compatibilité pour une attaque simple. La liste Actions permet d'en créer autant que nécessaire.",
    "bouton_saut": "Bouton de saut runtime principal, également utilisé par le buffer/coyote time.",
    "bouton_special": "Raccourci de compatibilité pour un pouvoir spécial. Les combinaisons avancées se définissent dans Actions.",
    "deadzone": "Réserve pour périphériques analogiques/futurs. Le pad DMS-1 standard est numérique.",
    "action_nom": "Nom lisible de l'action : SAUT, SABRE, DASH, BOMBE, INTERAGIR… Il n'y a pas de limite pratique au nombre d'actions d'auteur.",
    "action_entree": "Entrée DMS. Une touche : A, B, PLUS ou ×. Combinaison simultanée : A+B, A+PLUS, DOWN+A, etc. PLUS désigne le bouton + quand il participe à une combinaison.",
    "action_sources": "États depuis lesquels l'action est autorisée, séparés par |. Exemple : IDLE|WALK. Mets * pour tous les états.",
    "action_destination": "État atteint quand l'action est déclenchée. Laisser vide transforme l'action en métadonnée sans transition runtime automatique.",
    "action_runtime": "Effet runtime exécuté au moment de la transition. SAUTER applique l'impulsion de saut ; AUCUNE ne fait que changer d'état.",
    "action_attaque": "Association d'auteur. Pour qu'elle frappe réellement, l'attaque choisie doit être liée au même état offensif que la destination de l'action.",
    "action_projectile": "Association d'auteur. Pour déclencher un projectile avec une action, lie le projectile à l'état destination de cette action ; l'association seule ne force pas le tir.",
    "action_sequence": "Notation libre pour documenter une séquence complexe (ex. ↓↘→+A). Le runtime de base compile touche pressée, tenue, relâchée et combinaisons simultanées ; les séquences directionnelles temporisées restent des hooks avancés.",
    "fenetre_ms": "Métadonnée pour une future séquence temporisée. Elle n'affecte pas encore une combinaison simultanée simple comme A+B.",
    "maintien_ms": "Métadonnée de durée minimale de maintien. BOUTON_TENU teste actuellement si la touche/combinaison est tenue, sans minuterie dédiée.",
    "action_cooldown_ms": "Métadonnée de cooldown propre à l'action. Les cooldowns réellement exécutés aujourd'hui sont ceux des attaques/projectiles.",
    "pv_max": "Points de vie maximum de l'acteur.",
    "pv_depart": "Points de vie au spawn.",
    "degats_contact": "Dégâts infligés simplement par contact avec cet acteur.",
    "invincibilite_ms": "Fenêtre d'invincibilité après avoir subi un coup.",
    "recul_recu_x": "Impulsion X reçue quand l'acteur prend des dégâts.",
    "recul_recu_y": "Impulsion Y reçue quand l'acteur prend des dégâts.",
    "groupe_collision": "Famille de collision utilisée pour savoir qui peut toucher qui.",
    "equipe": "Équipe logique d'auteur. Utile pour organiser héros, ennemis, neutres et comportements futurs.",
    "etat_initial": "État actif au spawn. Il doit exister dans l'onglet États.",
    "etat_nom": "Un ÉTAT décrit ce que l'acteur est en train de faire maintenant : IDLE, WALK, JUMP, ATTACK, HURT, DEATH…",
    "animation": "Animation DRES jouée pendant cet état.",
    "boucle": "Rejoue l'animation tant que l'acteur reste dans cet état.",
    "multiplicateur_vitesse": "Multiplie la vitesse de déplacement pendant cet état. Exemple : 0 pendant une attaque immobile, 1.5 pendant une course.",
    "collision_monde": "Active les collisions avec sols/murs du niveau pendant cet état.",
    "controlable": "Autorise la manette à déplacer l'acteur pendant cet état.",
    "invulnerable": "L'acteur ne reçoit pas de dégâts pendant cet état.",
    "intangible": "L'acteur n'est plus considéré comme une cible physique normale pendant cet état.",
    "verrou_direction": "Empêche un changement d'orientation automatique pendant l'état, utile pour attaques/dash.",
    "duree_ms": "Durée logique optionnelle de l'état. 0 = pas de limite automatique ; utilise une transition TEMPS_ETAT ou ANIMATION_TERMINEE.",
    "source": "État de départ de la transition.",
    "destination": "État d'arrivée. Une transition répond à la question : « quand quitte-t-on l'état source, et pour aller où ? »",
    "condition": "Événement ou test qui autorise la transition : bouton, vitesse, sol, fin d'animation, PV, distance joueur…",
    "parametre_a": "Premier paramètre de la condition. Pour BOUTON_PRESSE/RELACHE : A, B, PLUS, × ou une combinaison comme A+B.",
    "operateur": "Comparaison appliquée entre la valeur observée et Valeur B.",
    "parametre_b": "Valeur comparée : 1/0 pour un booléen, millisecondes pour TEMPS_ETAT, pixels pour une distance, etc.",
    "priorite": "Plus le nombre est petit, plus la transition est prioritaire si plusieurs conditions deviennent vraies la même frame.",
    "transition_action": "Action immédiate attachée à la transition. SAUTER applique réellement l'impulsion de saut.",
    "attaque_nom": "Nom de l'attaque. Une attaque décrit ce que le coup FAIT ; le contrôle décrit comment on la déclenche.",
    "forme": "Source de zone d'attaque. DRES_HITBOX utilise les boîtes du sprite ; les autres formes servent à documenter/étendre des cas spéciaux.",
    "degats": "Quantité de PV retirée quand l'attaque touche une cible compatible.",
    "recul_x": "Recul horizontal infligé à la cible.",
    "recul_y": "Recul vertical infligé à la cible.",
    "stun_ms": "Durée pendant laquelle la cible touchée est étourdie.",
    "cooldown_ms": "Délai minimal avant que cette attaque puisse de nouveau toucher.",
    "startup_ms": "Préparation avant la fenêtre active de l'attaque. Sert au didacticiel/timeline et à la documentation de gameplay.",
    "active_ms": "Fenêtre pendant laquelle le coup est considéré actif dans la timeline d'auteur.",
    "recovery_ms": "Récupération après la partie active. Aide à calibrer le rythme et les cancels futurs.",
    "hitbox_nom": "Nom de la boîte offensive à utiliser dans le DRES/Asset Lab quand plusieurs boîtes sont disponibles.",
    "groupe_cible": "Groupes que le coup peut endommager.",
    "mode": "Comportement IA de base. Les profils spécialisés peuvent ensuite ajouter états/transitions/capteurs propres au jeu.",
}

HELP_TOPICS = {
    "Vue d'ensemble": """L'Actor Builder construit une fiche d'acteur complète. La chaîne la plus importante est :\n\nENTRÉE → ACTION → ÉTAT → TRANSITION → ANIMATION → ATTAQUE\n\n• Identité relie l'acteur à son DRES.\n• Mouvement règle sa locomotion.\n• Contrôles / actions dit ce que le joueur peut demander.\n• États dit ce que l'acteur est en train de faire.\n• Transitions dit quand il change d'état.\n• Attaques décrit les dégâts et le recul d'un état offensif.\n\nLes profils ne sont que des points de départ : tout reste modifiable.""",
    "États": """Un état est une situation courante : IDLE, WALK, JUMP, FALL, ATTACK, HURT, DEATH…\n\nChaque état choisit notamment une animation, l'usage de la gravité, les collisions monde et si le joueur garde le contrôle.\n\nExemple : ATTACK peut jouer SABRE_1, bloquer le contrôle et verrouiller la direction jusqu'à la fin de l'animation.""",
    "Transitions": """Une transition relie deux états avec une condition.\n\nExemples :\nIDLE → WALK si ENTREE_JOUEUR != 0\nJUMP → FALL si VITESSE_Y >= 0\nATTACK → IDLE si ANIMATION_TERMINEE == 1\nHURT → IDLE si TEMPS_ETAT >= 250 ms\n\nLa priorité la plus petite gagne si plusieurs transitions sont vraies pendant la même frame.""",
    "Actions": """Une Action répond à « que peut demander le joueur ? ».\n\nTu peux en créer autant que nécessaire. Une action peut être déclenchée par A, B, PLUS, × ou une combinaison simultanée comme A+B ou DOWN+A.\n\nElle peut générer automatiquement une transition vers un état. Exemple :\nSABRE : B, depuis IDLE|WALK, destination ATTACK_SABRE.\n\nLe champ Séquence permet de documenter un geste plus complexe ; les séquences temporisées restent réservées aux extensions avancées du runtime.""",
    "Attaques": """Une Attaque ne décrit pas le bouton : elle décrit le coup.\n\nElle est liée à un état offensif et contient dégâts, recul, stun, cooldown, cible et hitbox. La timeline Préparation / Active / Récupération aide à comprendre le rythme du coup.\n\nExemple : Action SABRE (B) → État ATTACK_SABRE → Animation SABRE_1 → Attaque SABRE_L1.""",
    "Mouvement": """Le runtime acteur prend désormais en compte les déplacements HORIZONTAL, VERTICAL, LIBRE_2D, 4_DIRECTIONS et 8_DIRECTIONS avec vitesses, accélérations et freinages X/Y.\n\nLa gravité, la chute, le saut, le coyote time et le jump buffer restent séparés du déplacement top-down/SHMUP.\n\nLes options plus spécifiques comme masse, nage, grimpe ou règles particulières de plateformes restent des données d'auteur tant qu'un jeu n'active pas leur logique spécialisée.""",
    "Lecture rapide": """Pour un acteur simple :\n1. Choisir un profil.\n2. Lier le DRES dans Identité.\n3. Régler Mouvement.\n4. Ajouter les Actions joueur.\n5. Vérifier les États.\n6. Vérifier les Transitions automatiques et explicites.\n7. Créer les Attaques éventuelles.\n8. Lancer Diagnostic puis exporter .dactor.""",
}


def parse_button_mask(value):
    raw = str(value or "").strip().upper().replace(" ", "")
    if not raw:
        return 0
    if raw in BUTTON_BITS:
        return BUTTON_BITS[raw]
    # PLUS désigne le bouton + lorsqu'il participe à une combinaison.
    raw = raw.replace("++", "+PLUS+")
    parts = [p for p in re.split(r"[+&]", raw) if p]
    mask = 0
    for part in parts:
        if part not in BUTTON_BITS:
            return 0
        mask |= BUTTON_BITS[part]
    return mask


def default_action(name="ACTION"):
    return {
        "nom": name, "categorie": "PERSONNALISE", "declencheur": "BOUTON_PRESSE", "entree": "A",
        "sources": "*", "destination": "", "action_runtime": "AUCUNE", "attaque": "", "projectile": "",
        "sequence": "", "fenetre_ms": 180, "maintien_ms": 0, "cooldown_ms": 0, "priorite": 20,
        "consommer_entree": True, "commentaire": ""
    }


def action(name, category, entry, sources="*", dest="", runtime="AUCUNE", attack_name="", projectile_name="", prio=20):
    a = default_action(name)
    a.update({"categorie": category, "entree": entry, "sources": sources, "destination": dest,
              "action_runtime": runtime, "attaque": attack_name, "projectile": projectile_name, "priorite": prio})
    return a


def base_actor(profile="Personnalisé"):
    return {
        "nom": "ACTEUR", "type": "ENNEMI", "profil": profile, "description": "",
        "ressource_dres": "", "ressource_dcoll": "", "animations_detectees": [],
        "mouvement": {
            "axe": "HORIZONTAL", "vitesse_max_x": 2.0, "vitesse_max_y": 2.0,
            "acceleration_x": 0.25, "acceleration_y": 0.25, "freinage_x": 0.2, "freinage_y": 0.2,
            "gravite": 0.25, "vitesse_chute_max": 6.0, "vitesse_saut": -5.0, "nombre_sauts": 1,
            "coyote_ms": 80, "buffer_saut_ms": 100, "masse": 1.0, "peut_tomber": True,
            "traverse_plateformes": False, "pousser_objets": False,
            "dash_actif": False, "dash_vitesse": 5.0, "dash_ms": 140, "dash_cooldown_ms": 300,
            "grimpe": False, "vitesse_grimpe": 1.5, "nage": False, "vitesse_nage": 1.5,
            "vol": False, "vitesse_vol": 2.0,
            "actif_hors_ecran": False, "detruire_hors_ecran": False, "marge_activation": 32,
            "respawn": False, "respawn_ms": 3000
        },
        "controle": {
            "joueur": False, "diagonales": False, "bouton_action": "A", "bouton_attaque": "B",
            "bouton_saut": "+", "bouton_special": "×", "deadzone": 8
        },
        "combat": {
            "pv_max": 3, "pv_depart": 3, "degats_contact": 0, "invincibilite_ms": 500,
            "recul_recu_x": 1.0, "recul_recu_y": -1.0, "groupe_collision": "ENNEMI", "equipe": "ENNEMI",
            "mort_detruit": True, "delai_destruction_ms": 0
        },
        "ia": {
            "mode": "AUCUNE", "distance_detection": 96, "distance_perte": 160, "vitesse_patrouille": 1.0,
            "distance_patrouille": 64, "retourner_mur": True, "retourner_vide": True, "suivre_x": True,
            "suivre_y": False, "delai_decision_ms": 250, "chance_action": 100
        },
        "interaction": {"active": False, "type": "PARLER", "distance": 16, "bouton": "A", "texte_id": "", "item_requis": "", "item_donne": "", "flag_requis": "", "flag_active": "", "evenement": "", "une_fois": False},
        "rpg": {"niveau": 1, "xp": 0, "attaque": 10, "defense": 5, "magie": 0, "agilite": 5, "chance": 0, "equipe_id": "NEUTRE", "loot_table": "", "xp_donne": 0},
        "puzzle": {"peut_porter": False, "peut_etre_pousse": False, "poids": 1, "cle_id": "", "serrure_id": "", "switch_id": "", "flag_requis": "", "flag_active": ""},
        "etat_initial": "IDLE", "actions": [], "etats": [], "transitions": [], "attaques": [], "projectiles": [],
        "capteurs": [], "variables": [], "tags": "", "notes": ""
    }


def st(n, anim=None, loop=True, grav=True, world=True, ctl=True, inv=False, intang=False, duree=0, mult=1.0):
    return {"nom": n, "animation": anim or n, "boucle": loop, "vitesse_animation": 1.0, "multiplicateur_vitesse": mult,
            "gravite": grav, "collision_monde": world, "controlable": ctl, "invulnerable": inv, "intangible": intang,
            "verrou_direction": False, "duree_ms": duree, "sfx_entree": "", "sfx_sortie": "", "tags": ""}


def tr(src, dst, cond, a="", op="==", b="", prio=100, runtime_action="AUCUNE", aa="", ab=""):
    return {"source": src, "destination": dst, "condition": cond, "parametre_a": a, "operateur": op, "parametre_b": b,
            "priorite": prio, "action": runtime_action, "action_a": aa, "action_b": ab,
            "consommer_entree": False, "active": True, "commentaire": ""}


def attack(n, state, deg=1, cible="ENNEMI"):
    return {"nom": n, "etat": state, "forme": "DRES_HITBOX", "degats": deg, "recul_x": 1.5, "recul_y": 0.0,
            "stun_ms": 120, "cooldown_ms": 300, "startup_ms": 80, "active_ms": 100, "recovery_ms": 160,
            "groupe_cible": cible, "hitbox_nom": "ATTACK", "perce_armure": False, "multi_hit": False,
            "hitstop_ms": 0, "sfx": "", "commentaire": ""}


def projectile(n, actor_path, grp, direction="FACE", vit=4.0, rate=250):
    return {"nom": n, "acteur": actor_path, "etat": "", "direction": direction, "vitesse": vit, "angle": 0.0,
            "cadence_ms": rate, "max_simultane": 4, "offset_x": 0, "offset_y": 0, "degats": 1,
            "groupe": grp, "detruire_sur_collision": True, "duree_vie_ms": 2500}


def sensor(n, t=None, dist=8, ox=0, oy=0):
    return {"nom": n, "type": t or n, "distance": dist, "largeur": 8, "hauteur": 8, "offset_x": ox, "offset_y": oy, "actif": True, "commentaire": ""}


def var(n, t="ENTIER", v="0", p=False):
    return {"nom": n, "type": t, "valeur_initiale": v, "persistante": p, "commentaire": ""}


def actor_from_profile(p):
    a = base_actor(p)
    if p == "Personnage plateforme":
        a["type"] = "JOUEUR"; a["controle"].update({"joueur": True, "diagonales": False}); a["combat"].update({"groupe_collision": "JOUEUR", "equipe": "JOUEUR", "pv_max": 5, "pv_depart": 5})
        a["mouvement"].update({"axe": "HORIZONTAL", "vitesse_max_x": 2.5, "vitesse_saut": -5.2})
        a["etats"] = [st("IDLE"), st("WALK"), st("JUMP", loop=False), st("FALL"), st("ATTACK", loop=False, ctl=False), st("HURT", loop=False, ctl=False, inv=True, duree=250, mult=.4), st("DEATH", loop=False, world=False, ctl=False, inv=True, intang=True, duree=800, mult=0)]
        a["transitions"] = [tr("IDLE", "WALK", "ENTREE_JOUEUR", "GAUCHE_DROITE", "!=", "0", 20), tr("WALK", "IDLE", "ENTREE_JOUEUR", "GAUCHE_DROITE", "==", "0", 20), tr("JUMP", "FALL", "VITESSE_Y", "Y", ">=", "0", 10), tr("FALL", "IDLE", "AU_SOL", "", "==", "1", 5), tr("ATTACK", "IDLE", "ANIMATION_TERMINEE", "", "==", "1", 20)]
        a["actions"] = [action("SAUT", "MOUVEMENT", "+", "IDLE|WALK", "JUMP", "SAUTER", prio=5), action("ATTAQUE", "ATTAQUE", "B", "IDLE|WALK", "ATTACK", attack_name="ATTAQUE_1", prio=6), action("INTERAGIR", "INTERACTION", "A", "IDLE|WALK"), action("SPECIAL", "SPECIAL", "×", "IDLE|WALK")]
        a["attaques"] = [attack("ATTAQUE_1", "ATTACK", 1, "ENNEMI")]
    elif p == "Ennemi plateforme":
        a["mouvement"].update({"axe": "HORIZONTAL", "vitesse_max_x": 1.2}); a["combat"].update({"pv_max": 2, "pv_depart": 2}); a["ia"].update({"mode": "PATROUILLE", "vitesse_patrouille": .8, "distance_patrouille": 48})
        a["etats"] = [st("IDLE", ctl=False), st("WALK", ctl=False), st("ATTACK", loop=False, ctl=False), st("HURT", loop=False, ctl=False, inv=True, duree=200), st("DEATH", loop=False, world=False, ctl=False, intang=True, duree=500)]
        a["transitions"] = [tr("IDLE", "WALK", "TEMPS_ETAT", "ms", ">=", "400", 40), tr("WALK", "ATTACK", "JOUEUR_DISTANCE", "px", "<=", "32", 5), tr("ATTACK", "WALK", "ANIMATION_TERMINEE", "", "==", "1", 20)]
        a["capteurs"] = [sensor("SOL_DEVANT", "SOL_DEVANT", 12, 8, 8), sensor("MUR_DEVANT", "MUR_DEVANT", 8, 8, 0)]
        a["attaques"] = [attack("CONTACT_ATTAQUE", "ATTACK", 1, "JOUEUR")]
    elif p == "Joueur shoot'em up":
        a["type"] = "JOUEUR"; a["controle"].update({"joueur": True, "diagonales": True}); a["combat"].update({"groupe_collision": "JOUEUR", "equipe": "JOUEUR"})
        a["mouvement"].update({"axe": "8_DIRECTIONS", "gravite": 0, "peut_tomber": False, "vitesse_max_x": 3, "vitesse_max_y": 3, "acceleration_x": .5, "acceleration_y": .5, "actif_hors_ecran": True})
        a["etats"] = [st("FLY", grav=False, world=False), st("FIRE", "FLY", grav=False, world=False), st("HURT", loop=False, grav=False, world=False, ctl=False, inv=True, duree=300), st("DEATH", loop=False, grav=False, world=False, ctl=False, intang=True, duree=1000)]; a["etat_initial"] = "FLY"
        a["transitions"] = [tr("FIRE", "FLY", "TOUJOURS", "", "==", "1", 1)]
        shot = projectile("TIR_PRINCIPAL", "PLAYER_SHOT.dactor", "PROJECTILE_JOUEUR", "DROITE", 5, 120); shot["etat"] = "FIRE"; a["projectiles"] = [shot]
        fire = action("TIR", "ATTAQUE", "A", "FLY", "FIRE", projectile_name="TIR_PRINCIPAL", prio=10); fire["declencheur"] = "BOUTON_TENU"
        a["actions"] = [fire, action("TIR_FORT", "ATTAQUE", "A+B", "FLY"), action("BOMBE", "SPECIAL", "B", "FLY"), action("SPECIAL", "SPECIAL", "×", "FLY")]
    elif p == "Ennemi shoot'em up":
        a["mouvement"].update({"axe": "LIBRE_2D", "gravite": 0, "peut_tomber": False, "detruire_hors_ecran": True}); a["ia"]["mode"] = "SHMUP_LIGNE"
        a["etats"] = [st("FLY", grav=False, world=False, ctl=False), st("ATTACK", loop=False, grav=False, world=False, ctl=False), st("DEATH", loop=False, grav=False, world=False, ctl=False, intang=True, duree=500)]; a["etat_initial"] = "FLY"
        a["projectiles"] = [projectile("TIR", "ENEMY_SHOT.dactor", "PROJECTILE_ENNEMI", "VERS_JOUEUR", 3, 800)]
    elif p in ("Joueur beat'em up", "Ennemi beat'em up"):
        player = p.startswith("Joueur"); a["type"] = "JOUEUR" if player else "ENNEMI"; a["controle"].update({"joueur": player, "diagonales": True})
        a["combat"].update({"groupe_collision": "JOUEUR" if player else "ENNEMI", "equipe": "JOUEUR" if player else "ENNEMI", "pv_max": 10 if player else 6, "pv_depart": 10 if player else 6})
        a["mouvement"].update({"axe": "8_DIRECTIONS", "gravite": 0, "peut_tomber": False, "vitesse_max_x": 2.2, "vitesse_max_y": 1.6})
        if not player: a["ia"].update({"mode": "BEAT_EM_UP", "suivre_y": True})
        a["etats"] = [st("IDLE", grav=False), st("WALK", grav=False), st("ATTACK_1", loop=False, grav=False, ctl=False), st("ATTACK_2", loop=False, grav=False, ctl=False), st("HURT", loop=False, grav=False, ctl=False, inv=True, duree=250), st("DEATH", loop=False, grav=False, ctl=False, intang=True, duree=900)]
        target = "ENNEMI" if player else "JOUEUR"
        a["attaques"] = [attack("POING", "ATTACK_1", 2, target), attack("COUP_FORT", "ATTACK_2", 4, target)]
        a["transitions"] = [tr("ATTACK_1", "IDLE", "ANIMATION_TERMINEE", "", "==", "1", 20), tr("ATTACK_2", "IDLE", "ANIMATION_TERMINEE", "", "==", "1", 20)]
        if player:
            a["actions"] = [action("POING", "ATTAQUE", "A", "IDLE|WALK", "ATTACK_1", attack_name="POING", prio=8), action("COUP_FORT", "ATTAQUE", "B", "IDLE|WALK", "ATTACK_2", attack_name="COUP_FORT", prio=7), action("ATTAQUE_COMBINEE", "ATTAQUE", "A+B", "IDLE|WALK", "ATTACK_2", attack_name="COUP_FORT", prio=5), action("SPECIAL", "SPECIAL", "×", "IDLE|WALK")]
    elif p in ("Exploration / aventure", "Joueur RPG"):
        a["type"] = "JOUEUR"; a["controle"].update({"joueur": True, "diagonales": True}); a["combat"].update({"groupe_collision": "JOUEUR", "equipe": "JOUEUR"})
        a["mouvement"].update({"axe": "8_DIRECTIONS", "gravite": 0, "peut_tomber": False, "vitesse_max_x": 2, "vitesse_max_y": 2})
        a["etats"] = [st("IDLE", grav=False), st("WALK", grav=False), st("INTERACT", loop=False, grav=False, ctl=False), st("HURT", loop=False, grav=False, ctl=False, inv=True, duree=250)]
        a["interaction"].update({"active": True, "type": "UTILISER"}); a["actions"] = [action("INTERAGIR", "INTERACTION", "A", "IDLE|WALK", "INTERACT"), action("ACTION_2", "SPECIAL", "B", "IDLE|WALK")]
        if p == "Joueur RPG": a["variables"] = [var("or", "ENTIER", "0", True), var("mana", "ENTIER", "10", True)]; a["rpg"]["equipe_id"] = "JOUEUR"
    elif p == "PNJ / dialogue":
        a["type"] = "PNJ"; a["mouvement"].update({"axe": "AUCUN", "gravite": 0, "peut_tomber": False}); a["ia"]["mode"] = "NPC"; a["combat"].update({"groupe_collision": "PNJ", "equipe": "NEUTRE", "pv_max": 1, "pv_depart": 1})
        a["etats"] = [st("IDLE", grav=False, ctl=False)]; a["interaction"].update({"active": True, "type": "PARLER", "distance": 20, "texte_id": "DIALOGUE_001"})
    elif p == "Ennemi RPG":
        a["mouvement"].update({"axe": "8_DIRECTIONS", "gravite": 0, "peut_tomber": False}); a["ia"].update({"mode": "RPG", "suivre_y": True}); a["combat"].update({"pv_max": 8, "pv_depart": 8}); a["rpg"].update({"equipe_id": "ENNEMI", "loot_table": "LOOT_BASIC", "xp_donne": 20})
        a["etats"] = [st("IDLE", grav=False, ctl=False), st("CHASE", "WALK", grav=False, ctl=False), st("ATTACK", loop=False, grav=False, ctl=False), st("DEATH", loop=False, grav=False, ctl=False, intang=True, duree=700)]
        a["attaques"] = [attack("ATTAQUE", "ATTACK", 2, "JOUEUR")]
    elif p == "Objet puzzle":
        a["type"] = "OBJET_INTERACTIF"; a["mouvement"].update({"axe": "AUCUN", "gravite": 0, "peut_tomber": False}); a["combat"].update({"groupe_collision": "OBJET", "equipe": "NEUTRE", "pv_max": 1, "pv_depart": 1})
        a["puzzle"].update({"peut_etre_pousse": True}); a["etats"] = [st("OFF", grav=False, ctl=False), st("ON", grav=False, ctl=False)]; a["etat_initial"] = "OFF"; a["variables"] = [var("active", "BOOLEEN", "false")]; a["interaction"].update({"active": True, "type": "ACTIVER"})
    elif p == "Projectile":
        a["type"] = "PROJECTILE"; a["mouvement"].update({"axe": "LIBRE_2D", "gravite": 0, "peut_tomber": False, "detruire_hors_ecran": True}); a["combat"].update({"groupe_collision": "PROJECTILE_JOUEUR", "equipe": "JOUEUR", "pv_max": 1, "pv_depart": 1}); a["etats"] = [st("FLY", grav=False, world=False, ctl=False)]; a["etat_initial"] = "FLY"; a["variables"] = [var("duree_vie_ms", "ENTIER", "2000")]
    elif p == "Boss":
        a["type"] = "BOSS"; a["combat"].update({"pv_max": 100, "pv_depart": 100, "groupe_collision": "BOSS", "equipe": "ENNEMI"}); a["ia"]["mode"] = "BOSS_PHASES"
        a["etats"] = [st("INTRO", loop=False, ctl=False, duree=1000), st("PHASE_1", ctl=False), st("PHASE_2", ctl=False), st("HURT", loop=False, ctl=False, inv=True, duree=200), st("DEATH", loop=False, ctl=False, intang=True, duree=1800)]
        a["etat_initial"] = "INTRO"; a["transitions"] = [tr("INTRO", "PHASE_1", "TEMPS_ETAT", "ms", ">=", "1000", 10), tr("PHASE_1", "PHASE_2", "PV_INFERIEUR_OU_EGAL", "PV", "<=", "50", 2), tr("PHASE_2", "DEATH", "PV_INFERIEUR_OU_EGAL", "PV", "<=", "0", 1)]
    else:
        a["etats"] = [st("IDLE")]
    return a


def _merge_defaults(dst, defaults):
    if not isinstance(dst, dict):
        return deepcopy(defaults)
    out = deepcopy(dst)
    for key, value in defaults.items():
        if key not in out:
            out[key] = deepcopy(value)
        elif isinstance(value, dict):
            out[key] = _merge_defaults(out.get(key), value)
    return out


def normalize_actor(raw):
    a = _merge_defaults(raw if isinstance(raw, dict) else {}, base_actor(str((raw or {}).get("profil", "Personnalisé")) if isinstance(raw, dict) else "Personnalisé"))
    a["etats"] = [_merge_defaults(x, st(str(x.get("nom", "STATE")))) for x in a.get("etats", [])]
    a["transitions"] = [_merge_defaults(x, tr("IDLE", "IDLE", "TOUJOURS")) for x in a.get("transitions", [])]
    a["attaques"] = [_merge_defaults(x, attack(str(x.get("nom", "ATTAQUE")), str(x.get("etat", "ATTACK")))) for x in a.get("attaques", [])]
    a["projectiles"] = [_merge_defaults(x, projectile(str(x.get("nom", "TIR")), str(x.get("acteur", "")), str(x.get("groupe", "PROJECTILE_ENNEMI")))) for x in a.get("projectiles", [])]
    a["capteurs"] = [_merge_defaults(x, sensor(str(x.get("nom", "CAPTEUR")))) for x in a.get("capteurs", [])]
    a["variables"] = [_merge_defaults(x, var(str(x.get("nom", "variable")))) for x in a.get("variables", [])]
    had_actions = "actions" in (raw or {}) if isinstance(raw, dict) else False
    a["actions"] = [_merge_defaults(x, default_action(str(x.get("nom", "ACTION")))) for x in a.get("actions", [])]
    if not had_actions and a.get("controle", {}).get("joueur"):
        seen = set()
        for t in a.get("transitions", []):
            if str(t.get("condition", "")).upper() not in ("BOUTON_PRESSE", "BOUTON_RELACHE"):
                continue
            entry = str(t.get("parametre_a", "")).strip()
            if not entry:
                continue
            key = (entry, t.get("source"), t.get("destination"))
            if key in seen:
                continue
            seen.add(key)
            name = f"ACTION_{entry}_{t.get('destination', '')}".strip("_")
            a["actions"].append(action(name, "PERSONNALISE", entry, str(t.get("source", "*")), str(t.get("destination", "")), str(t.get("action", "AUCUNE")), prio=int(t.get("priorite", 20) or 20)))
    return a


def materialize_actions(a):
    out = deepcopy(a)
    states = [str(s.get("nom", "")) for s in out.get("etats", [])]
    existing = {(str(t.get("source", "")), str(t.get("destination", "")), str(t.get("condition", "")), str(t.get("parametre_a", "")), str(t.get("action", "AUCUNE"))) for t in out.get("transitions", [])}
    for ac in out.get("actions", []):
        trigger = str(ac.get("declencheur", "BOUTON_PRESSE")).upper()
        dest = str(ac.get("destination", "")).strip()
        entry = str(ac.get("entree", "")).strip()
        if trigger not in ("BOUTON_PRESSE", "BOUTON_TENU", "BOUTON_RELACHE") or not dest or not entry:
            continue
        src_txt = str(ac.get("sources", "*")).strip()
        sources = states if src_txt in ("", "*") else [x.strip() for x in src_txt.split("|") if x.strip()]
        for src in sources:
            key = (src, dest, trigger, entry, str(ac.get("action_runtime", "AUCUNE")))
            if key in existing:
                continue
            t = tr(src, dest, trigger, entry, "==", "1", int(ac.get("priorite", 20) or 20), str(ac.get("action_runtime", "AUCUNE")))
            t["consommer_entree"] = bool(ac.get("consommer_entree", True)); t["commentaire"] = f"Générée depuis action : {ac.get('nom', 'ACTION')}"; t["generee_depuis_action"] = str(ac.get("nom", "ACTION"))
            out.setdefault("transitions", []).append(t); existing.add(key)
    return out


def resolve_relative(value, project_path=None):
    raw = str(value or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute() and project_path:
        p = (Path(project_path).parent / p).resolve()
    return p


def detect_animations(path):
    p = Path(path); found = set()
    if not p.exists():
        return []
    try:
        texts = []
        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                if "manifest.json" in z.namelist():
                    try:
                        m = json.loads(z.read("manifest.json").decode("utf-8"))
                        for n in (m.get("animations") or {}): found.add(str(n).upper())
                    except Exception: pass
                for n in z.namelist():
                    if n.lower().endswith((".h", ".json", ".txt")):
                        try: texts.append(z.read(n).decode("utf-8", errors="ignore"))
                        except Exception: pass
        else:
            texts = [p.read_text(encoding="utf-8", errors="ignore")]
        for txt in texts:
            for m in re.finditer(r"ANIM_([A-Z0-9_]+)_(?:FIRST|COUNT)", txt): found.add(m.group(1))
            for m in re.finditer(r'"animation"\s*:\s*"([^"]+)"', txt, re.I): found.add(m.group(1).upper())
    except Exception:
        pass
    return sorted(found)


def validate(a):
    e, w = [], []
    runtime = materialize_actions(a)
    names = [str(x.get("nom", "")) for x in a.get("etats", [])]
    if not str(a.get("nom", "")).strip(): e.append("Nom acteur vide.")
    if not names: e.append("Aucun état.")
    if a.get("etat_initial") not in names: e.append("État initial introuvable.")
    if len(names) != len(set(names)): e.append("Noms d'états en double.")
    for i, t in enumerate(runtime.get("transitions", []), 1):
        if t.get("source") not in names: e.append(f"Transition {i}: source absente ({t.get('source')}).")
        if t.get("destination") not in names: e.append(f"Transition {i}: destination absente ({t.get('destination')}).")
        if str(t.get("condition", "")).upper() in ("BOUTON_PRESSE", "BOUTON_TENU", "BOUTON_RELACHE") and not parse_button_mask(t.get("parametre_a")):
            e.append(f"Transition {i}: bouton/combinaison non reconnue ({t.get('parametre_a')}).")
    if int(a.get("combat", {}).get("pv_depart", 0)) > int(a.get("combat", {}).get("pv_max", 0)): w.append("PV départ > PV max.")
    if a.get("controle", {}).get("joueur") and a.get("ia", {}).get("mode") != "AUCUNE": w.append("Contrôle joueur et IA actifs en même temps.")
    if a.get("type") in ("JOUEUR", "ENNEMI", "BOSS") and not a.get("ressource_dres"): w.append("Aucun DRES lié.")
    det = set(a.get("animations_detectees", []))
    if det:
        for s in a.get("etats", []):
            if s.get("animation") and str(s.get("animation")).upper() not in det: w.append(f"Animation non détectée : {s.get('animation')} ({s.get('nom')}).")
    for at in a.get("attaques", []):
        if at.get("etat") not in names: w.append(f"Attaque {at.get('nom')} liée à un état absent.")
    action_names = [str(x.get("nom", "")) for x in a.get("actions", [])]
    if len(action_names) != len(set(action_names)): w.append("Noms d'actions en double.")
    for i, ac in enumerate(a.get("actions", []), 1):
        if str(ac.get("declencheur", "")).upper() in ("BOUTON_PRESSE", "BOUTON_TENU", "BOUTON_RELACHE") and not parse_button_mask(ac.get("entree")):
            if str(ac.get("sequence", "")).strip(): w.append(f"Action {i} ({ac.get('nom')}): séquence documentée mais entrée runtime non compilable.")
            else: e.append(f"Action {i} ({ac.get('nom')}): entrée runtime non reconnue ({ac.get('entree')}).")
        dest = str(ac.get("destination", "")).strip()
        if dest and dest not in names: e.append(f"Action {i} ({ac.get('nom')}): état destination absent ({dest}).")
        src_txt = str(ac.get("sources", "*")).strip()
        if src_txt not in ("", "*"):
            for src in [x.strip() for x in src_txt.split("|") if x.strip()]:
                if src not in names: e.append(f"Action {i} ({ac.get('nom')}): état source absent ({src}).")
        attack_name = str(ac.get("attaque", "")).strip()
        if attack_name:
            linked = next((x for x in a.get("attaques", []) if str(x.get("nom", "")) == attack_name), None)
            if not linked: w.append(f"Action {i} ({ac.get('nom')}): attaque associée introuvable ({attack_name}).")
            elif dest and str(linked.get("etat", "")) != dest: w.append(f"Action {i} ({ac.get('nom')}): l'attaque {attack_name} est liée à {linked.get('etat')} mais l'action va vers {dest}.")
        projectile_name = str(ac.get("projectile", "")).strip()
        if projectile_name:
            linked = next((x for x in a.get("projectiles", []) if str(x.get("nom", "")) == projectile_name), None)
            if not linked: w.append(f"Action {i} ({ac.get('nom')}): projectile associé introuvable ({projectile_name}).")
            elif dest and str(linked.get("etat", "")).strip() and str(linked.get("etat", "")) != dest: w.append(f"Action {i} ({ac.get('nom')}): le projectile {projectile_name} tire dans l'état {linked.get('etat')} mais l'action va vers {dest}.")
    return e, w


def portable_actor(a, target, source_base=None):
    out = deepcopy(a); target = Path(target).resolve(); source = Path(source_base).resolve() if source_base else None
    def portable(value):
        raw = str(value or "").strip()
        if not raw: return raw
        p = Path(raw)
        if not p.is_absolute() and source is not None: p = (source / p).resolve()
        if not p.is_absolute(): return raw.replace("\\", "/")
        return os.path.relpath(p, target.parent).replace("\\", "/")
    out["ressource_dres"] = portable(out.get("ressource_dres")); out["ressource_dcoll"] = portable(out.get("ressource_dcoll"))
    for pr in out.get("projectiles", []): pr["acteur"] = portable(pr.get("acteur"))
    return out


def export_dactor(path, a, source_base=None):
    authored = portable_actor(a, path, source_base)
    compiled = materialize_actions(authored)
    e, w = validate(authored)
    manifest = {"format": "DACTOR", "format_version": 1, "generator": f"{APP_NAME} {APP_VERSION}", "actor": compiled,
                "authoring": {"action_count": len(authored.get("actions", [])), "generated_transition_count": max(0, len(compiled.get("transitions", [])) - len(authored.get("transitions", [])))},
                "contracts": {"dres": "sprites/animations/boîtes par frame", "dcoll": "collision monde", "gdk": "compile/interprète DACTOR"}}
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(a.get("nom", "ACTOR")).upper()).strip("_") or "ACTOR"
    header = ["#pragma once", "", f"#define {safe}_STATE_COUNT {len(compiled.get('etats', []))}", f"#define {safe}_TRANSITION_COUNT {len(compiled.get('transitions', []))}", f"#define {safe}_ACTION_COUNT {len(compiled.get('actions', []))}", f"#define {safe}_ATTACK_COUNT {len(compiled.get('attaques', []))}", f"#define {safe}_PROJECTILE_COUNT {len(compiled.get('projectiles', []))}"]
    for i, s in enumerate(compiled.get("etats", [])): header.append(f"#define {safe}_STATE_{re.sub(r'[^A-Za-z0-9_]+', '_', str(s.get('nom','')).upper())} {i}")
    report = ["DMS ACTOR BUILDER - RAPPORT", "===========================", f"Acteur : {a.get('nom')}", f"Type : {a.get('type')}", f"Profil : {a.get('profil')}", f"Actions auteur : {len(a.get('actions', []))}", f"États : {len(a.get('etats', []))}", f"Transitions explicites : {len(a.get('transitions', []))}", f"Transitions exportées : {len(compiled.get('transitions', []))}", f"Attaques : {len(a.get('attaques', []))}", f"Projectiles : {len(a.get('projectiles', []))}", "", "ERREURS", *(["Aucune"] if not e else ["* " + x for x in e]), "", "AVERTISSEMENTS", *(["Aucun"] if not w else ["* " + x for x in w])]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False)); z.writestr("actor.json", json.dumps(compiled, indent=2, ensure_ascii=False)); z.writestr("authoring_actor.json", json.dumps(authored, indent=2, ensure_ascii=False)); z.writestr("report.txt", "\n".join(report)); z.writestr(safe.lower() + ".h", "\n".join(header)); z.writestr("README.txt", "DACTOR V1 - acteur DMS-1.\nLes Actions d'auteur simples génèrent des transitions runtime au moment de l'export.\nLe runtime accepte les touches DMS et les combinaisons simultanées.\n")


def parse(v, typ):
    s = v.get()
    if typ == "int":
        try: return int(float(s))
        except Exception: return 0
    if typ == "float":
        try: return float(s)
        except Exception: return 0.0
    if typ == "bool": return bool(v.get())
    return s


def rgb333_to_hex(word):
    r = (word >> 6) & 7; g = (word >> 3) & 7; b = word & 7
    rr = int(round(r * 255 / 7)); gg = int(round(g * 255 / 7)); bb = int(round(b * 255 / 7))
    return f"#{rr:02x}{gg:02x}{bb:02x}"


class DRESPreviewData:
    def __init__(self, path):
        self.path = Path(path); self.manifest = {}; self.tiles = b""; self.palettes = []; self.cell_size = 8
        with zipfile.ZipFile(self.path) as z:
            self.manifest = json.loads(z.read("manifest.json").decode("utf-8")); self.tiles = z.read("tiles.bin"); pal = z.read("palettes.bin")
        self.cell_size = int(self.manifest.get("analysis_cell_size", 8) or 8)
        words = [struct.unpack_from(">H", pal, i)[0] for i in range(0, len(pal) - 1, 2)]
        self.palettes = [[rgb333_to_hex(w) for w in words[i:i + 16]] for i in range(0, len(words), 16)]
        self.frames = self.manifest.get("frames") or []; self.cells = self.manifest.get("cells") or []; self.animations = self.manifest.get("animations") or {}

    def frame_pixels(self, frame_id, transparent="#202329"):
        if not (0 <= frame_id < len(self.frames)): return 1, 1, [[transparent]], {}
        frame = self.frames[frame_id]; w = max(1, int(frame.get("width", 1))); h = max(1, int(frame.get("height", 1)))
        pix = [[transparent for _ in range(w)] for _ in range(h)]; npx = self.cell_size * self.cell_size; tile_bytes = (npx + 1) // 2
        for c in self.cells:
            if int(c.get("frame", -1)) != frame_id or c.get("empty") or c.get("tile") is None: continue
            tile_id = int(c.get("tile")); off = tile_id * tile_bytes; raw = self.tiles[off:off + tile_bytes]
            vals = []
            for b in raw: vals.extend([(b >> 4) & 15, b & 15])
            vals = vals[:npx]
            matrix = [vals[y * self.cell_size:(y + 1) * self.cell_size] for y in range(self.cell_size)]
            if c.get("flip_x"): matrix = [list(reversed(row)) for row in matrix]
            if c.get("flip_y"): matrix = list(reversed(matrix))
            pid = c.get("palette"); palette = self.palettes[int(pid)] if pid is not None and 0 <= int(pid) < len(self.palettes) else ["#000000"] * 16
            x0, y0 = int(c.get("x", 0)), int(c.get("y", 0)); cw, ch = int(c.get("w", self.cell_size)), int(c.get("h", self.cell_size))
            for yy in range(min(ch, self.cell_size)):
                for xx in range(min(cw, self.cell_size)):
                    dx, dy = x0 + xx, y0 + yy
                    if 0 <= dx < w and 0 <= dy < h:
                        idx = matrix[yy][xx]
                        if idx != 0: pix[dy][dx] = palette[idx] if idx < len(palette) else "#ffffff"
        return w, h, pix, frame

    def animation_frames(self, name):
        if not self.frames: return []
        wanted = str(name or "").upper()
        for key, ids in self.animations.items():
            if str(key).upper() == wanted: return [int(x) for x in ids]
        return [0]


class PreviewPanel(ttk.LabelFrame):
    def __init__(self, parent, title="Aperçu sprite", size=320):
        super().__init__(parent, text=title, padding=8); self.size = size; self.data = None; self.path = None; self.after_id = None; self.anim_name = ""; self.anim_ids = []; self.anim_pos = 0; self._photo = None
        self.canvas = tk.Canvas(self, width=size, height=size, bg="#202329", highlightthickness=0); self.canvas.pack(fill="both", expand=True)
        self.info = ttk.Label(self, text="Lie un DRES pour afficher le sprite.", style="Sub.TLabel", wraplength=size); self.info.pack(fill="x", pady=(6, 0))

    def stop(self):
        if self.after_id:
            try: self.after_cancel(self.after_id)
            except Exception: pass
            self.after_id = None

    def load(self, path):
        self.stop(); self.data = None; self.path = None; self.canvas.delete("all")
        p = Path(path) if path else None
        if not p or not p.exists() or not zipfile.is_zipfile(p):
            self.canvas.create_text(self.size // 2, self.size // 2, text="Aucun aperçu DRES", fill="#9aa3ad"); self.info.configure(text="Lie un DRES valide pour afficher les animations."); return
        try:
            self.data = DRESPreviewData(p); self.path = p; self.info.configure(text=f"{p.name} • {len(self.data.frames)} frame(s) • {len(self.data.animations)} animation(s)"); self.show_animation(self.anim_name or next(iter(self.data.animations), ""))
        except Exception as ex:
            self.canvas.create_text(self.size // 2, self.size // 2, text="Aperçu indisponible", fill="#e0b0b0"); self.info.configure(text=str(ex))

    def show_animation(self, name, note=""):
        self.stop(); self.anim_name = str(name or "")
        if not self.data:
            self.canvas.delete("all"); self.canvas.create_text(self.size // 2, self.size // 2, text="Aucun aperçu DRES", fill="#9aa3ad");
            if note: self.info.configure(text=note)
            return
        self.anim_ids = self.data.animation_frames(self.anim_name); self.anim_pos = 0; self._draw_current(note)

    def _draw_current(self, note=""):
        if not self.data or not self.anim_ids: return
        fid = self.anim_ids[self.anim_pos % len(self.anim_ids)]; w, h, pix, frame = self.data.frame_pixels(fid)
        self.canvas.delete("all"); img = tk.PhotoImage(width=w, height=h)
        for y, row in enumerate(pix): img.put("{" + " ".join(row) + "}", to=(0, y))
        scale = max(1, min(10, int((self.size - 30) / max(w, h))))
        self._photo = img.zoom(scale, scale)
        dw, dh = w * scale, h * scale; ox = max(0, (self.size - dw) // 2); oy = max(0, (self.size - dh) // 2)
        self.canvas.create_image(ox, oy, anchor="nw", image=self._photo)
        hb = frame.get("hitbox") or {}
        if hb.get("enabled"):
            x1 = ox + int(hb.get("x", 0)) * scale; y1 = oy + int(hb.get("y", 0)) * scale; x2 = x1 + int(hb.get("w", 0)) * scale; y2 = y1 + int(hb.get("h", 0)) * scale
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#f1cf5a", width=2)
        base = f"Animation : {self.anim_name or frame.get('animation','')} • frame {fid + 1}/{len(self.data.frames)} • {w}×{h}px"
        self.info.configure(text=base + (("\n" + note) if note else ""))
        if len(self.anim_ids) > 1:
            delay = max(40, int(frame.get("duration_ms", 120) or 120)); self.after_id = self.after(delay, lambda: self._next(note))

    def _next(self, note=""):
        self.anim_pos = (self.anim_pos + 1) % len(self.anim_ids); self._draw_current(note)


class AttackPreview(PreviewPanel):
    def __init__(self, parent, size=300):
        super().__init__(parent, "Aperçu attaque", size); self.timeline = tk.Canvas(self, height=76, bg="#17191d", highlightthickness=0); self.timeline.pack(fill="x", pady=(8, 0))

    def show_attack(self, at, state_anim=""):
        at = at or {}
        self.show_animation(state_anim or at.get("etat", ""), f"Attaque : {at.get('nom','')} • hitbox : {at.get('hitbox_nom','ATTACK')}")
        self.timeline.delete("all"); w = max(240, self.timeline.winfo_width() or self.size)
        startup = max(0, int(at.get("startup_ms", 0) or 0)); active = max(0, int(at.get("active_ms", 0) or 0)); recovery = max(0, int(at.get("recovery_ms", 0) or 0)); total = max(1, startup + active + recovery)
        parts = [("Préparation", startup, "#555b65"), ("ACTIVE", active, "#8a5a5a"), ("Récupération", recovery, "#4f6658")]
        x = 4
        for label, value, color in parts:
            ww = max(1, int((w - 8) * value / total)); self.timeline.create_rectangle(x, 12, x + ww, 46, fill=color, outline="#222"); self.timeline.create_text(x + ww / 2, 29, text=label, fill="white"); x += ww
        self.timeline.create_text(4, 61, anchor="w", text=f"{startup} ms  |  {active} ms  |  {recovery} ms", fill="#b9c0c8")


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent); self.canvas = tk.Canvas(self, bg="#17191d", highlightthickness=0); sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview); self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw"); self.canvas.configure(yscrollcommand=sb.set); self.canvas.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))); self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))


class ListEditor(ttk.Frame):
    def __init__(self, parent, title, data_getter, schema, display, changed, help_callback=None, on_select=None, intro=""):
        super().__init__(parent); self.getter = data_getter; self.schema = schema; self.display = display; self.changed = changed; self.help_callback = help_callback; self.on_select = on_select; self.sel = None; self.choice_widgets = {}
        lf = ttk.LabelFrame(self, text=title, padding=6); lf.pack(fill="both", expand=True)
        if intro: ttk.Label(lf, text=intro, style="Sub.TLabel", wraplength=900).pack(fill="x", pady=(0, 6))
        body = ttk.Panedwindow(lf, orient="horizontal"); body.pack(fill="both", expand=True)
        treef = ttk.Frame(body); edit_outer = ttk.Frame(body); body.add(treef, weight=3); body.add(edit_outer, weight=2)
        cols = [x[0] for x in display[1:]]; self.tree = ttk.Treeview(treef, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text=display[0][1]); self.tree.column("#0", width=150)
        for k, lab in display[1:]: self.tree.heading(k, text=lab); self.tree.column(k, width=120)
        ysb = ttk.Scrollbar(treef, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=ysb.set); self.tree.pack(side="left", fill="both", expand=True); ysb.pack(side="right", fill="y"); self.tree.bind("<<TreeviewSelect>>", self.select)
        sf = ScrollFrame(edit_outer); sf.pack(fill="both", expand=True); edit = sf.inner; self.vars = {}
        for r, item in enumerate(schema):
            key, lab, typ, default, choices = item
            label = ttk.Label(edit, text=lab); label.grid(row=r, column=0, sticky="w", pady=2); self._bind_help(label, key, lab)
            if typ == "bool":
                variable = tk.BooleanVar(value=default); widget = ttk.Checkbutton(edit, variable=variable); widget.grid(row=r, column=1, sticky="w")
            else:
                variable = tk.StringVar(value=str(default))
                resolved = choices() if callable(choices) else choices
                if resolved is not None:
                    widget = ttk.Combobox(edit, textvariable=variable, values=resolved, state=("normal" if callable(choices) else "readonly"), width=22); self.choice_widgets[key] = (widget, choices)
                else:
                    widget = ttk.Entry(edit, textvariable=variable, width=24)
                widget.grid(row=r, column=1, padx=4, pady=2, sticky="ew")
            self._bind_help(widget, key, lab); q = ttk.Button(edit, text="?", width=2, command=lambda k=key, l=lab: self._show_help(k, l)); q.grid(row=r, column=2, padx=(2, 0)); self.vars[key] = variable
        edit.columnconfigure(1, weight=1)
        bar = ttk.Frame(edit); bar.grid(row=len(schema), column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Button(bar, text="+ Ajouter", command=self.add).pack(side="left"); ttk.Button(bar, text="Dupliquer", command=self.dup).pack(side="left", padx=3); ttk.Button(bar, text="Appliquer", command=self.apply).pack(side="left", padx=3); ttk.Button(bar, text="Supprimer", command=self.delete).pack(side="right")

    def _show_help(self, key, lab):
        if self.help_callback: self.help_callback(key, lab, True)
    def _bind_help(self, widget, key, lab):
        if self.help_callback: widget.bind("<Enter>", lambda e, k=key, l=lab: self.help_callback(k, l, False), add="+")
    def refresh(self):
        for key, (widget, choices) in self.choice_widgets.items():
            vals = choices() if callable(choices) else choices; widget.configure(values=vals or [])
        for i in self.tree.get_children(): self.tree.delete(i)
        data = self.getter()
        for i, x in enumerate(data): self.tree.insert("", "end", iid=str(i), text=x.get(self.display[0][0], ""), values=[x.get(k, "") for k, _ in self.display[1:]])
        if self.sel is not None and self.sel < len(data):
            self.tree.selection_set(str(self.sel)); self.tree.see(str(self.sel))
    def select(self, e=None):
        s = self.tree.selection()
        if not s: return
        self.sel = int(s[0]); x = self.getter()[self.sel]
        for k, lab, typ, default, choices in self.schema: self.vars[k].set(x.get(k, default))
        if self.on_select: self.on_select(x)
    def item_from_form(self): return {k: parse(self.vars[k], typ) for k, lab, typ, default, choices in self.schema}
    def add(self): self.changed(); self.getter().append(self.item_from_form()); self.sel = len(self.getter()) - 1; self.refresh(); self.select()
    def dup(self):
        if self.sel is None: return
        self.changed(); self.getter().append(deepcopy(self.getter()[self.sel])); self.sel = len(self.getter()) - 1; self.refresh(); self.select()
    def apply(self):
        if self.sel is None: return
        self.changed(); self.getter()[self.sel] = self.item_from_form(); self.refresh(); self.select()
    def delete(self):
        if self.sel is None: return
        self.changed(); del self.getter()[self.sel]; self.sel = None; self.refresh()


class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(f"{APP_NAME} - {APP_VERSION}"); self.geometry("1760x980"); self.minsize(1280, 760); self.configure(bg="#17191d")
        self.actor = actor_from_profile("Personnalisé"); self.project = None; self.undo_stack = []; self.redo_stack = []; self.preview_panels = []; self._saved_actor = deepcopy(self.actor)
        self.style(); self.build(); self.protocol("WM_DELETE_WINDOW", self.close_app); self.load_actor(); self.refresh(); self.after(150, self.refresh_previews)

    def style(self):
        s = ttk.Style(self)
        try: s.theme_use("clam")
        except Exception: pass
        s.configure(".", font=("Segoe UI", 9)); s.configure("TFrame", background="#17191d"); s.configure("TLabelframe", background="#17191d", foreground="#eee"); s.configure("TLabelframe.Label", background="#17191d", foreground="#eee"); s.configure("TLabel", background="#17191d", foreground="#ddd"); s.configure("Title.TLabel", background="#17191d", foreground="#fff", font=("Segoe UI", 17, "bold")); s.configure("Sub.TLabel", background="#17191d", foreground="#9aa3ad"); s.configure("Help.TLabel", background="#22262c", foreground="#d8dde3", padding=7); s.configure("TCheckbutton", background="#17191d", foreground="#ddd"); s.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

    def push(self):
        self.sync(); self.undo_stack.append(deepcopy(self.actor)); self.undo_stack = self.undo_stack[-40:]; self.redo_stack.clear()
    def undo(self):
        if not self.undo_stack: return
        self.sync(); self.redo_stack.append(deepcopy(self.actor)); self.actor = self.undo_stack.pop(); self.load_actor(); self.refresh(); self.refresh_previews()
    def redo(self):
        if not self.redo_stack: return
        self.sync(); self.undo_stack.append(deepcopy(self.actor)); self.actor = self.redo_stack.pop(); self.load_actor(); self.refresh(); self.refresh_previews()

    def build(self):
        top = ttk.Frame(self, padding=(12, 8)); top.pack(fill="x"); ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left"); ttk.Label(top, text="V1.1 • aide intégrée • aperçu DRES • actions illimitées • mouvement 2D runtime", style="Sub.TLabel").pack(side="left", padx=14)
        ttk.Button(top, text="? Aide / didacticiel", command=self.show_tutorial).pack(side="right", padx=4); ttk.Button(top, text="Exporter .dactor", command=self.export, style="Accent.TButton").pack(side="right", padx=4); ttk.Button(top, text="Sauver", command=self.save).pack(side="right", padx=4); ttk.Button(top, text="Ouvrir", command=self.open).pack(side="right", padx=4); ttk.Button(top, text="Nouveau depuis profil", command=self.new_profile).pack(side="right", padx=8); ttk.Button(top, text="↶", width=3, command=self.undo).pack(side="right"); ttk.Button(top, text="↷", width=3, command=self.redo).pack(side="right", padx=2)
        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 6)); self.tabs = {}
        for name in ["Identité", "Mouvement", "Contrôles / actions", "Combat", "États", "Transitions", "Attaques", "Projectiles", "IA / capteurs", "Hooks interaction / RPG", "Variables (hooks C)", "Diagnostic / export"]:
            f = ttk.Frame(self.nb, padding=10); self.nb.add(f, text=name); self.tabs[name] = f
        self.identity(); self.movement(); self.controls_actions(); self.combat(); self.lists(); self.interact(); self.diagnostic()
        helpbar = ttk.Frame(self, padding=(12, 0, 12, 5)); helpbar.pack(fill="x"); self.help_var = tk.StringVar(value="Survole un réglage ou clique sur ? pour obtenir son explication."); ttk.Label(helpbar, textvariable=self.help_var, style="Help.TLabel", wraplength=1500).pack(fill="x")
        bottom = ttk.Frame(self, padding=(12, 0, 12, 9)); bottom.pack(fill="x"); self.status = ttk.Label(bottom, text="Prêt."); self.status.pack(side="left"); ttk.Label(bottom, text="Profils = points de départ ; tout reste modifiable.", style="Sub.TLabel").pack(side="right")

    def help_for(self, key, label, popup=False):
        text = HELP_FIELDS.get(key) or f"{label} : réglage de l'acteur. Modifie cette valeur puis utilise Diagnostic pour vérifier la cohérence avant export."
        self.help_var.set(text)
        if popup: messagebox.showinfo(f"Aide - {label}", text, parent=self)

    def bind_help(self, widget, key, label): widget.bind("<Enter>", lambda e: self.help_for(key, label, False), add="+")

    def form(self, parent, schema, section=None):
        lf = ttk.LabelFrame(parent, text=section or "Réglages", padding=8); lf.pack(fill="x", pady=4); out = {}
        for i, (key, lab, typ, default, choices) in enumerate(schema):
            labw = ttk.Label(lf, text=lab); labw.grid(row=i, column=0, sticky="w", pady=3); self.bind_help(labw, key, lab)
            if typ == "bool":
                v = tk.BooleanVar(value=default); w = ttk.Checkbutton(lf, variable=v); w.grid(row=i, column=1, sticky="w")
            else:
                v = tk.StringVar(value=str(default)); w = ttk.Combobox(lf, textvariable=v, values=choices, state="readonly") if choices is not None else ttk.Entry(lf, textvariable=v); w.grid(row=i, column=1, sticky="ew", padx=5)
            self.bind_help(w, key, lab); ttk.Button(lf, text="?", width=2, command=lambda k=key, l=lab: self.help_for(k, l, True)).grid(row=i, column=2); out[key] = v
        lf.columnconfigure(1, weight=1); return out

    def identity(self):
        t = self.tabs["Identité"]; pan = ttk.Panedwindow(t, orient="horizontal"); pan.pack(fill="both", expand=True); left = ttk.Frame(pan); mid = ttk.Frame(pan); right = ttk.Frame(pan); pan.add(left, weight=3); pan.add(mid, weight=3); pan.add(right, weight=4)
        self.ident = self.form(left, [("nom", "Nom", "str", "ACTEUR", None), ("type", "Type", "str", "ENNEMI", TYPES), ("profil", "Profil", "str", "Personnalisé", PROFILS)], "Identité de l'acteur")
        desc = ttk.LabelFrame(left, text="Description", padding=8); desc.pack(fill="both", expand=True, pady=5); self.description_text = tk.Text(desc, height=9, bg="#202329", fg="#eee", insertbackground="#eee", relief="flat", wrap="word"); self.description_text.pack(fill="both", expand=True); self.bind_help(self.description_text, "description", "Description")
        self.res = self.form(mid, [("dres", "Sprite / DRES", "str", "", None), ("dcoll", "Collision monde / DCOLL", "str", "", None)], "Ressources")
        ttk.Button(mid, text="Choisir DRES / header", command=self.choose_dres).pack(fill="x", pady=3); ttk.Button(mid, text="Analyser animations", command=self.scan).pack(fill="x", pady=3)
        ttk.Label(mid, text="Animations détectées", style="Sub.TLabel").pack(anchor="w", pady=(8, 2)); self.anim = tk.Listbox(mid, bg="#202329", fg="#eee", height=16); self.anim.pack(fill="both", expand=True)
        ttk.Label(mid, text="L'aperçu est reconstruit directement depuis tiles.bin + palettes.bin du DRES : aucun PNG externe n'est requis.", style="Sub.TLabel", wraplength=430).pack(anchor="w", pady=6)
        self.preview_identity = PreviewPanel(right, "Aperçu principal", 400); self.preview_identity.pack(fill="both", expand=True); self.preview_panels.append(self.preview_identity)

    def movement(self):
        t = self.tabs["Mouvement"]; sf = ScrollFrame(t); sf.pack(fill="both", expand=True); root = sf.inner; c1 = ttk.Frame(root); c2 = ttk.Frame(root); c3 = ttk.Frame(root); c1.grid(row=0, column=0, sticky="nsew", padx=(0, 6)); c2.grid(row=0, column=1, sticky="nsew", padx=6); c3.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        for i in (0, 1, 2):
            root.columnconfigure(i, weight=1)
        self.mov = {}
        self.mov.update(self.form(c1, [("axe", "Mode de locomotion", "str", "HORIZONTAL", AXES), ("vitesse_max_x", "Vitesse max X", "float", 2, None), ("vitesse_max_y", "Vitesse max Y", "float", 2, None), ("acceleration_x", "Accélération X", "float", .25, None), ("acceleration_y", "Accélération Y", "float", .25, None), ("freinage_x", "Freinage X", "float", .2, None), ("freinage_y", "Freinage Y", "float", .2, None), ("masse", "Masse / poids logique", "float", 1, None)], "Déplacement principal"))
        self.mov.update(self.form(c2, [("gravite", "Gravité", "float", .25, None), ("vitesse_chute_max", "Vitesse chute max", "float", 6, None), ("vitesse_saut", "Vitesse saut", "float", -5, None), ("nombre_sauts", "Nombre de sauts", "int", 1, None), ("coyote_ms", "Coyote time ms", "int", 80, None), ("buffer_saut_ms", "Buffer saut ms", "int", 100, None), ("peut_tomber", "Peut tomber", "bool", True, None), ("traverse_plateformes", "Traverse plateformes", "bool", False, None), ("pousser_objets", "Peut pousser objets", "bool", False, None)], "Sol / saut / collisions"))
        self.mov.update(self.form(c3, [("dash_actif", "Dash disponible", "bool", False, None), ("dash_vitesse", "Vitesse dash", "float", 5, None), ("dash_ms", "Durée dash ms", "int", 140, None), ("dash_cooldown_ms", "Cooldown dash ms", "int", 300, None), ("grimpe", "Grimpe / échelle", "bool", False, None), ("vitesse_grimpe", "Vitesse grimpe", "float", 1.5, None), ("nage", "Nage", "bool", False, None), ("vitesse_nage", "Vitesse nage", "float", 1.5, None), ("vol", "Vol", "bool", False, None), ("vitesse_vol", "Vitesse vol", "float", 2, None), ("actif_hors_ecran", "Actif hors écran", "bool", False, None), ("detruire_hors_ecran", "Détruire hors écran", "bool", False, None), ("marge_activation", "Marge activation px", "int", 32, None), ("respawn", "Respawn danger", "bool", False, None), ("respawn_ms", "Délai respawn ms", "int", 3000, None)], "Capacités / monde"))
        ttk.Label(root, text="Exécuté directement par le runtime : locomotion X/Y, diagonales, accélérations/freinages, gravité, chute, saut, coyote/buffer et règles hors écran. Dash, nage, grimpe, vol et poussée sont conservés comme capacités d'auteur à brancher par les systèmes spécialisés du jeu.", style="Sub.TLabel", wraplength=1450).grid(row=1, column=0, columnspan=3, sticky="ew", pady=10)

    def controls_actions(self):
        t = self.tabs["Contrôles / actions"]; pan = ttk.Panedwindow(t, orient="vertical"); pan.pack(fill="both", expand=True); top = ttk.Frame(pan); bottom = ttk.Frame(pan); pan.add(top, weight=1); pan.add(bottom, weight=4)
        l = ttk.Frame(top); r = ttk.Frame(top); l.pack(side="left", fill="both", expand=True, padx=(0,6)); r.pack(side="left", fill="both", expand=True, padx=(6,0))
        self.ctrl = {}; self.ctrl.update(self.form(l, [("joueur", "Contrôlé par joueur", "bool", False, None), ("diagonales", "Diagonales autorisées", "bool", False, None), ("deadzone", "Deadzone", "int", 8, None)], "Contrôle runtime")); self.ctrl.update(self.form(r, [("bouton_action", "Raccourci Action", "str", "A", PAD_BUTTONS), ("bouton_attaque", "Raccourci Attaque", "str", "B", PAD_BUTTONS), ("bouton_saut", "Raccourci Saut", "str", "+", PAD_BUTTONS), ("bouton_special", "Raccourci Spécial", "str", "×", PAD_BUTTONS)], "Compatibilité / raccourcis"))
        action_schema = [("nom", "Nom action", "str", "ACTION", None), ("categorie", "Catégorie", "str", "PERSONNALISE", ACTION_CATEGORIES), ("declencheur", "Déclencheur", "str", "BOUTON_PRESSE", ACTION_TRIGGERS), ("entree", "Entrée / combinaison", "str", "A", None), ("sources", "États autorisés (|)", "str", "*", None), ("destination", "État destination", "str", "", lambda: self.state_names()), ("action_runtime", "Action runtime", "str", "AUCUNE", RUNTIME_ACTIONS), ("attaque", "Attaque associée", "str", "", None), ("projectile", "Projectile associé (via état)", "str", "", None), ("sequence", "Séquence avancée (méta)", "str", "", None), ("fenetre_ms", "Fenêtre séquence ms (méta)", "int", 180, None), ("maintien_ms", "Maintien mini ms (méta)", "int", 0, None), ("cooldown_ms", "Cooldown action ms (méta)", "int", 0, None), ("priorite", "Priorité", "int", 20, None), ("consommer_entree", "Consommer entrée", "bool", True, None), ("commentaire", "Commentaire", "str", "", None)]
        # Alias de clés pour aides spécifiques.
        action_schema = [("nom" if k == "nom" else k, lab, typ, default, choices) for k, lab, typ, default, choices in action_schema]
        self.ed_actions = ListEditor(bottom, "Actions joueur - autant que nécessaire", lambda: self.actor["actions"], action_schema, [("nom", "Action"), ("entree", "Entrée"), ("sources", "Depuis"), ("destination", "Vers")], self.push, self.action_help, intro="Une Action = ce que le joueur demande. Les actions avec une destination génèrent automatiquement leurs transitions au moment de l'export."); self.ed_actions.pack(fill="both", expand=True)

    def action_help(self, key, label, popup=False):
        alias = {"nom":"action_nom", "entree":"action_entree", "sources":"action_sources", "destination":"action_destination", "action_runtime":"action_runtime", "attaque":"action_attaque", "projectile":"action_projectile", "sequence":"action_sequence", "cooldown_ms":"action_cooldown_ms"}.get(key, key); self.help_for(alias, label, popup)

    def combat(self):
        t = self.tabs["Combat"]; l = ttk.Frame(t); r = ttk.Frame(t); l.pack(side="left", fill="both", expand=True, padx=(0, 6)); r.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.cmb = {}; self.cmb.update(self.form(l, [("pv_max", "PV max", "int", 3, None), ("pv_depart", "PV départ", "int", 3, None), ("degats_contact", "Dégâts contact", "int", 0, None), ("invincibilite_ms", "Invincibilité ms", "int", 500, None), ("recul_recu_x", "Recul reçu X", "float", 1, None), ("recul_recu_y", "Recul reçu Y", "float", -1, None)], "Résistance / réception")); self.cmb.update(self.form(r, [("groupe_collision", "Groupe collision", "str", "ENNEMI", GROUPES), ("equipe", "Équipe logique", "str", "ENNEMI", ["NEUTRE","JOUEUR","ENNEMI"]), ("mort_detruit", "Mort détruit acteur", "bool", True, None), ("delai_destruction_ms", "Délai destruction ms", "int", 0, None)], "Équipe / destruction"))
        ttk.Label(r, text="Contrôle ≠ Attaque : le bouton se règle dans Actions ; les propriétés du coup se règlent dans Attaques.", style="Sub.TLabel", wraplength=600).pack(fill="x", pady=10)

    def state_names(self): return [str(x.get("nom", "")) for x in self.actor.get("etats", []) if str(x.get("nom", ""))]
    def state_anim(self, state_name):
        for s in self.actor.get("etats", []):
            if str(s.get("nom", "")) == str(state_name): return str(s.get("animation", ""))
        return str(state_name or "")

    def lists(self):
        state_schema = [("nom", "Nom", "str", "IDLE", None), ("animation", "Animation", "str", "IDLE", lambda: self.actor.get("animations_detectees", [])), ("boucle", "Boucle", "bool", True, None), ("vitesse_animation", "Vitesse animation", "float", 1, None), ("multiplicateur_vitesse", "Multiplicateur mouvement", "float", 1, None), ("gravite", "Gravité", "bool", True, None), ("collision_monde", "Collision monde", "bool", True, None), ("controlable", "Contrôlable", "bool", True, None), ("invulnerable", "Invulnérable", "bool", False, None), ("intangible", "Intangible", "bool", False, None), ("verrou_direction", "Verrou direction", "bool", False, None), ("duree_ms", "Durée ms", "int", 0, None), ("sfx_entree", "SFX entrée", "str", "", None), ("sfx_sortie", "SFX sortie", "str", "", None)]
        trans_schema = [("source", "Source", "str", "IDLE", lambda: self.state_names()), ("destination", "Destination", "str", "IDLE", lambda: self.state_names()), ("condition", "Condition", "str", "TOUJOURS", CONDITIONS), ("parametre_a", "Paramètre A / bouton", "str", "", None), ("operateur", "Opérateur", "str", "==", ["==", "!=", ">", ">=", "<", "<="]), ("parametre_b", "Valeur B", "str", "", None), ("priorite", "Priorité", "int", 100, None), ("action", "Action immédiate", "str", "AUCUNE", RUNTIME_ACTIONS), ("active", "Active", "bool", True, None), ("commentaire", "Commentaire", "str", "", None)]
        attack_schema = [("nom", "Nom", "str", "ATTAQUE_1", None), ("etat", "État offensif", "str", "ATTACK", lambda: self.state_names()), ("forme", "Forme / source", "str", "DRES_HITBOX", FORMES_ATTAQUE), ("hitbox_nom", "Nom hitbox", "str", "ATTACK", None), ("degats", "Dégâts", "int", 1, None), ("recul_x", "Recul X", "float", 1.5, None), ("recul_y", "Recul Y", "float", 0, None), ("stun_ms", "Stun ms", "int", 120, None), ("cooldown_ms", "Cooldown ms", "int", 300, None), ("startup_ms", "Préparation ms", "int", 80, None), ("active_ms", "Active ms", "int", 100, None), ("recovery_ms", "Récupération ms", "int", 160, None), ("groupe_cible", "Groupe cible", "str", "ENNEMI", GROUPES), ("perce_armure", "Perce armure", "bool", False, None), ("multi_hit", "Multi-hit", "bool", False, None), ("hitstop_ms", "Hitstop ms", "int", 0, None), ("sfx", "SFX", "str", "", None), ("commentaire", "Commentaire", "str", "", None)]
        proj_schema = [("nom", "Nom", "str", "TIR", None), ("acteur", "Acteur .dactor", "str", "", None), ("etat", "État de tir", "str", "", lambda: self.state_names()), ("direction", "Direction", "str", "FACE", DIRECTIONS), ("vitesse", "Vitesse", "float", 4, None), ("cadence_ms", "Cadence ms", "int", 250, None), ("max_simultane", "Max simultané", "int", 4, None), ("offset_x", "Offset X", "int", 0, None), ("offset_y", "Offset Y", "int", 0, None), ("degats", "Dégâts", "int", 1, None), ("groupe", "Groupe", "str", "PROJECTILE_JOUEUR", GROUPES), ("detruire_sur_collision", "Détruire collision", "bool", True, None), ("duree_vie_ms", "Durée vie ms", "int", 2500, None)]
        sensor_schema = [("nom", "Nom", "str", "SOL_DEVANT", None), ("type", "Type", "str", "SOL_DEVANT", CAPTEURS), ("distance", "Distance", "int", 8, None), ("largeur", "Largeur", "int", 8, None), ("hauteur", "Hauteur", "int", 8, None), ("offset_x", "Offset X", "int", 0, None), ("offset_y", "Offset Y", "int", 0, None), ("actif", "Actif", "bool", True, None), ("commentaire", "Commentaire", "str", "", None)]
        var_schema = [("nom", "Nom", "str", "compteur", None), ("type", "Type", "str", "ENTIER", TYPES_VAR), ("valeur_initiale", "Valeur initiale", "str", "0", None), ("persistante", "Persistante", "bool", False, None), ("commentaire", "Commentaire", "str", "", None)]

        ps = ttk.Panedwindow(self.tabs["États"], orient="horizontal"); ps.pack(fill="both", expand=True); sf = ttk.Frame(ps); pv = ttk.Frame(ps); ps.add(sf, weight=4); ps.add(pv, weight=2); self.ed_states = ListEditor(sf, "États", lambda: self.actor["etats"], state_schema, [("nom", "État"), ("animation", "Animation"), ("duree_ms", "Durée")], self.push, self.state_help, self.on_state_select, "Un état = ce que l'acteur est en train de faire maintenant."); self.ed_states.pack(fill="both", expand=True); self.preview_state = PreviewPanel(pv, "Sprite de l'état", 300); self.preview_state.pack(fill="both", expand=True); self.preview_panels.append(self.preview_state)

        pt = ttk.Panedwindow(self.tabs["Transitions"], orient="horizontal"); pt.pack(fill="both", expand=True); tf = ttk.Frame(pt); tv = ttk.Frame(pt); pt.add(tf, weight=4); pt.add(tv, weight=2); self.ed_trans = ListEditor(tf, "Transitions explicites", lambda: self.actor["transitions"], trans_schema, [("source", "Source"), ("destination", "Destination"), ("condition", "Condition"), ("priorite", "Priorité")], self.push, self.transition_help, self.on_transition_select, "Une transition = la règle qui fait passer d'un état à un autre. Les transitions générées par Actions apparaissent dans l'export mais ne polluent pas cette liste d'auteur."); self.ed_trans.pack(fill="both", expand=True); self.preview_transition = PreviewPanel(tv, "Aperçu état source", 280); self.preview_transition.pack(fill="both", expand=True); self.transition_sentence = ttk.Label(tv, text="Sélectionne une transition.", style="Help.TLabel", wraplength=360); self.transition_sentence.pack(fill="x", pady=8); self.preview_panels.append(self.preview_transition)

        pa = ttk.Panedwindow(self.tabs["Attaques"], orient="horizontal"); pa.pack(fill="both", expand=True); af = ttk.Frame(pa); av = ttk.Frame(pa); pa.add(af, weight=4); pa.add(av, weight=2); self.ed_attack = ListEditor(af, "Attaques", lambda: self.actor["attaques"], attack_schema, [("nom", "Attaque"), ("etat", "État"), ("degats", "Dégâts"), ("groupe_cible", "Cible")], self.push, self.attack_help, self.on_attack_select, "Une attaque = ce que le coup fait : dégâts, recul, stun, cible et fenêtre temporelle. Le bouton qui la déclenche se règle dans Actions."); self.ed_attack.pack(fill="both", expand=True); self.preview_attack = AttackPreview(av, 280); self.preview_attack.pack(fill="both", expand=True); ttk.Label(av, text="Préparation / Active / Récupération = calibration auteur. Le runtime générique V1 utilise encore la hitbox DRES pendant l’état offensif ; ces trois timings ne découpent pas encore automatiquement la hitbox.", style="Sub.TLabel", wraplength=380).pack(fill="x", pady=(6,0)); self.preview_panels.append(self.preview_attack)

        self.ed_proj = ListEditor(self.tabs["Projectiles"], "Projectiles", lambda: self.actor["projectiles"], proj_schema, [("nom", "Projectile"), ("acteur", "Acteur"), ("direction", "Direction"), ("cadence_ms", "Cadence")], self.push, self.help_for, intro="Décrit les projectiles que cet acteur peut émettre. Le projectile lui-même peut être un autre .dactor."); self.ed_proj.pack(fill="both", expand=True)
        iaf = ttk.Frame(self.tabs["IA / capteurs"]); iaf.pack(side="left", fill="both", expand=True, padx=(0, 6)); self.aiv = self.form(iaf, [("mode", "Mode IA", "str", "AUCUNE", IA), ("distance_detection", "Distance détection", "int", 96, None), ("distance_perte", "Distance perte", "int", 160, None), ("vitesse_patrouille", "Vitesse patrouille", "float", 1, None), ("distance_patrouille", "Distance patrouille", "int", 64, None), ("retourner_mur", "Demi-tour mur", "bool", True, None), ("retourner_vide", "Demi-tour vide", "bool", True, None), ("suivre_x", "Suivre X", "bool", True, None), ("suivre_y", "Suivre Y", "bool", False, None), ("delai_decision_ms", "Délai décision ms", "int", 250, None), ("chance_action", "Chance action %", "int", 100, None)], "IA runtime")
        sf2 = ttk.Frame(self.tabs["IA / capteurs"]); sf2.pack(side="left", fill="both", expand=True, padx=(6, 0)); self.ed_sensor = ListEditor(sf2, "Capteurs - métadonnées / hooks", lambda: self.actor["capteurs"], sensor_schema, [("nom", "Capteur"), ("type", "Type"), ("distance", "Distance")], self.push, self.help_for); self.ed_sensor.pack(fill="both", expand=True)
        self.ed_var = ListEditor(self.tabs["Variables (hooks C)"], "Variables personnalisées", lambda: self.actor["variables"], var_schema, [("nom", "Variable"), ("type", "Type"), ("valeur_initiale", "Initiale"), ("persistante", "Persistante")], self.push, self.help_for, intro="Réserve des données propres au gameplay quand les paramètres standards de l'acteur ne suffisent pas."); self.ed_var.pack(fill="both", expand=True)

    def state_help(self, key, label, popup=False): self.help_for("etat_nom" if key == "nom" else key, label, popup)
    def transition_help(self, key, label, popup=False): self.help_for("transition_action" if key == "action" else key, label, popup)
    def attack_help(self, key, label, popup=False): self.help_for("attaque_nom" if key == "nom" else key, label, popup)
    def on_state_select(self, s): self.preview_state.show_animation(s.get("animation", ""), f"État : {s.get('nom','')}")
    def on_transition_select(self, t):
        anim = self.state_anim(t.get("source", "")); self.preview_transition.show_animation(anim, f"Source : {t.get('source','')}")
        self.transition_sentence.configure(text=f"{t.get('source','?')}  →  {t.get('destination','?')}\nSI {t.get('condition','TOUJOURS')}  {t.get('parametre_a','')} {t.get('operateur','==')} {t.get('parametre_b','')}\nPriorité : {t.get('priorite',100)}")
    def on_attack_select(self, at): self.preview_attack.show_attack(at, self.state_anim(at.get("etat", "")))

    def interact(self):
        t = self.tabs["Hooks interaction / RPG"]; c1 = ttk.Frame(t); c2 = ttk.Frame(t); c3 = ttk.Frame(t); c1.pack(side="left", fill="both", expand=True, padx=4); c2.pack(side="left", fill="both", expand=True, padx=4); c3.pack(side="left", fill="both", expand=True, padx=4)
        self.intv = self.form(c1, [("active", "Active", "bool", False, None), ("type", "Type interaction", "str", "PARLER", None), ("distance", "Distance", "int", 16, None), ("bouton", "Bouton", "str", "A", PAD_BUTTONS), ("texte_id", "Texte / dialogue ID", "str", "", None), ("item_requis", "Item requis", "str", "", None), ("item_donne", "Item donné", "str", "", None), ("flag_requis", "Flag requis", "str", "", None), ("flag_active", "Flag activé", "str", "", None), ("evenement", "Événement", "str", "", None), ("une_fois", "Une seule fois", "bool", False, None)], "Interaction")
        self.rpgv = self.form(c2, [("niveau", "Niveau", "int", 1, None), ("xp", "XP", "int", 0, None), ("attaque", "Attaque", "int", 10, None), ("defense", "Défense", "int", 5, None), ("magie", "Magie", "int", 0, None), ("agilite", "Agilité", "int", 5, None), ("chance", "Chance", "int", 0, None), ("equipe_id", "Équipe", "str", "NEUTRE", None), ("loot_table", "Table loot", "str", "", None), ("xp_donne", "XP donnée", "int", 0, None)], "RPG")
        self.puzv = self.form(c3, [("peut_porter", "Peut être porté", "bool", False, None), ("peut_etre_pousse", "Peut être poussé", "bool", False, None), ("poids", "Poids", "int", 1, None), ("cle_id", "Clé ID", "str", "", None), ("serrure_id", "Serrure ID", "str", "", None), ("switch_id", "Switch ID", "str", "", None), ("flag_requis", "Flag requis", "str", "", None), ("flag_active", "Flag activé", "str", "", None)], "Puzzle")
        self.misc = self.form(c3, [("etat_initial", "État initial", "str", "IDLE", None), ("tags", "Tags", "str", "", None), ("notes", "Notes", "str", "", None)], "Divers")

    def diagnostic(self):
        t = self.tabs["Diagnostic / export"]; ttk.Button(t, text="VALIDER L'ACTEUR", command=self.run_validate, style="Accent.TButton").pack(fill="x"); self.diag = tk.Text(t, bg="#202329", fg="#eee", relief="flat", wrap="word"); self.diag.pack(fill="both", expand=True, pady=8); self.diag.configure(state="disabled")
        bar = ttk.Frame(t); bar.pack(fill="x"); ttk.Button(bar, text="Exporter .dactor", command=self.export, style="Accent.TButton").pack(side="left"); ttk.Button(bar, text="Exporter bundle GDK", command=self.bundle).pack(side="left", padx=5); ttk.Button(bar, text="Sauver projet", command=self.save).pack(side="right")

    def choose_dres(self):
        p = filedialog.askopenfilename(title="DRES / header", filetypes=[("DMS", "*.dres *.h *.json"), ("Tous", "*.*")])
        if p: self.res["dres"].set(p); self.scan(); self.refresh_previews()
    def scan(self):
        p = resolve_relative(self.res["dres"].get(), self.project)
        self.actor["animations_detectees"] = detect_animations(p) if p else []; self.anim.delete(0, "end")
        for x in self.actor["animations_detectees"]: self.anim.insert("end", x)
        self.refresh(); self.status.configure(text=f"{len(self.actor['animations_detectees'])} animation(s) détectée(s).")

    def load_form(self, vmap, data):
        for k, v in vmap.items():
            if k in data: v.set(data[k])
    def sync_form(self, vmap, data):
        for k, v in vmap.items():
            old = data.get(k); typ = "bool" if isinstance(old, bool) else "int" if isinstance(old, int) else "float" if isinstance(old, float) else "str"; data[k] = parse(v, typ)
    def load_actor(self):
        self.actor = normalize_actor(self.actor); a = self.actor; self.load_form(self.ident, a); self.description_text.delete("1.0", "end"); self.description_text.insert("1.0", str(a.get("description", ""))); self.load_form(self.res, {"dres": a.get("ressource_dres", ""), "dcoll": a.get("ressource_dcoll", "")}); self.load_form(self.mov, a["mouvement"]); self.load_form(self.ctrl, a["controle"]); self.load_form(self.cmb, a["combat"]); self.load_form(self.aiv, a["ia"]); self.load_form(self.intv, a["interaction"]); self.load_form(self.rpgv, a["rpg"]); self.load_form(self.puzv, a["puzzle"]); self.load_form(self.misc, {"etat_initial": a["etat_initial"], "tags": a["tags"], "notes": a["notes"]}); self.anim.delete(0, "end")
        for x in a.get("animations_detectees", []): self.anim.insert("end", x)
    def sync(self):
        a = self.actor; self.sync_form(self.ident, a); a["description"] = self.description_text.get("1.0", "end-1c"); a["ressource_dres"] = self.res["dres"].get(); a["ressource_dcoll"] = self.res["dcoll"].get(); self.sync_form(self.mov, a["mouvement"]); self.sync_form(self.ctrl, a["controle"]); self.sync_form(self.cmb, a["combat"]); self.sync_form(self.aiv, a["ia"]); self.sync_form(self.intv, a["interaction"]); self.sync_form(self.rpgv, a["rpg"]); self.sync_form(self.puzv, a["puzzle"]); a["etat_initial"] = self.misc["etat_initial"].get(); a["tags"] = self.misc["tags"].get(); a["notes"] = self.misc["notes"].get()
    def refresh(self):
        for ed in [self.ed_actions, self.ed_states, self.ed_trans, self.ed_attack, self.ed_proj, self.ed_sensor, self.ed_var]: ed.refresh()
    def refresh_previews(self):
        p = resolve_relative(self.res["dres"].get(), self.project)
        for panel in self.preview_panels:
            if p and panel.path and Path(panel.path) == p and panel.data: continue
            panel.load(p)
        if self.actor.get("etats"): self.preview_identity.show_animation(self.actor.get("etats", [])[0].get("animation", ""), "Aperçu du premier état")

    def has_unsaved_changes(self):
        try:self.sync()
        except Exception:return True
        return self.actor != self._saved_actor
    def confirm_discard(self):
        if not self.has_unsaved_changes():return True
        ans=messagebox.askyesnocancel(APP_NAME,"L'acteur contient des modifications non sauvées.\n\nSauver avant de continuer ?")
        if ans is None:return False
        if ans:self.save();return not self.has_unsaved_changes()
        return True
    def close_app(self):
        if self.confirm_discard():self.destroy()

    def new_profile(self):
        if not self.confirm_discard(): return
        d = tk.Toplevel(self); d.title("Nouveau depuis profil"); d.transient(self); d.grab_set(); f = ttk.Frame(d, padding=16); f.pack(); v = tk.StringVar(value=PROFILS[0]); ttk.Label(f, text="Profil de départ").pack(anchor="w"); ttk.Combobox(f, textvariable=v, values=PROFILS, state="readonly", width=32).pack(fill="x", pady=8)
        def go(): self.actor = actor_from_profile(v.get()); self._saved_actor=deepcopy(self.actor); self.project = None; self.undo_stack.clear(); self.redo_stack.clear(); self.load_actor(); self.refresh(); self.refresh_previews(); d.destroy(); self.status.configure(text=f"Profil chargé : {v.get()}")
        ttk.Button(f, text="Créer", command=go, style="Accent.TButton").pack(fill="x")

    def show_tutorial(self):
        d = tk.Toplevel(self); d.title("DMS Actor Builder - Aide / didacticiel"); d.geometry("980x680"); d.transient(self); root = ttk.Frame(d, padding=10); root.pack(fill="both", expand=True); left = ttk.Frame(root); right = ttk.Frame(root); left.pack(side="left", fill="y", padx=(0,10)); right.pack(side="left", fill="both", expand=True); lb = tk.Listbox(left, width=24, bg="#202329", fg="#eee"); lb.pack(fill="y", expand=True); txt = tk.Text(right, bg="#202329", fg="#eee", wrap="word", relief="flat", padx=14, pady=14, font=("Segoe UI", 11)); txt.pack(fill="both", expand=True)
        topics = list(HELP_TOPICS)
        for x in topics: lb.insert("end", x)
        def show(e=None):
            s = lb.curselection(); idx = s[0] if s else 0; txt.configure(state="normal"); txt.delete("1.0", "end"); txt.insert("1.0", HELP_TOPICS[topics[idx]]); txt.configure(state="disabled")
        lb.bind("<<ListboxSelect>>", show); lb.selection_set(0); show(); ttk.Button(right, text="Fermer", command=d.destroy).pack(anchor="e", pady=(8,0))

    def run_validate(self):
        self.sync(); e, w = validate(self.actor); compiled = materialize_actions(self.actor); txt = ["VALIDATION DMS ACTOR", "====================", "", "ERREURS", *(["Aucune"] if not e else ["• " + x for x in e]), "", "AVERTISSEMENTS", *(["Aucun"] if not w else ["• " + x for x in w]), "", "RÉSUMÉ", f"Actions auteur : {len(self.actor['actions'])}", f"États : {len(self.actor['etats'])}", f"Transitions explicites : {len(self.actor['transitions'])}", f"Transitions exportées : {len(compiled['transitions'])}", f"Attaques : {len(self.actor['attaques'])}", f"Projectiles : {len(self.actor['projectiles'])}", f"Capteurs : {len(self.actor['capteurs'])}", f"Variables : {len(self.actor['variables'])}"]
        self.diag.configure(state="normal"); self.diag.delete("1.0", "end"); self.diag.insert("1.0", "\n".join(txt)); self.diag.configure(state="disabled"); self.status.configure(text=f"Validation : {len(e)} erreur(s), {len(w)} avertissement(s)."); return e, w

    def save(self):
        self.sync(); target = Path(self.project) if self.project else None
        if target is None:
            p = filedialog.asksaveasfilename(title="Sauver acteur", defaultextension=".dactorproj", initialfile=self.actor["nom"] + ".dactorproj", filetypes=[("DMS Actor Project", "*.dactorproj")])
            if not p:return
            target=Path(p)
        try:
            source = Path(self.project).parent if self.project else None; portable = portable_actor(self.actor, target, source)
            tmp=target.with_name(target.name+".tmp");tmp.write_text(json.dumps({"format": "DMS_ACTOR_PROJECT", "version": 1, "app_version": APP_VERSION, "actor": portable}, indent=2, ensure_ascii=False), encoding="utf-8");os.replace(tmp,target)
            self.project=str(target);self._saved_actor=deepcopy(self.actor);self.status.configure(text=f"Sauvé : {target.name}")
        except Exception as ex:
            try:tmp.unlink(missing_ok=True)
            except Exception:pass
            messagebox.showerror("Sauvegarde",str(ex))
    def open(self):
        if not self.confirm_discard():return
        p = filedialog.askopenfilename(title="Ouvrir acteur", filetypes=[("DMS Actor Project", "*.dactorproj"), ("JSON", "*.json")])
        if not p: return
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"));
            if d.get("format") != "DMS_ACTOR_PROJECT": raise ValueError("Format invalide.")
            self.actor = normalize_actor(d["actor"]); self._saved_actor=deepcopy(self.actor); self.project = p; self.undo_stack.clear(); self.redo_stack.clear(); self.load_actor(); self.refresh(); self.refresh_previews(); self.status.configure(text=f"Ouvert : {Path(p).name}")
        except Exception as ex: messagebox.showerror("Ouverture", str(ex))
    def export(self):
        self.sync(); e, w = validate(self.actor)
        if e and not messagebox.askyesno("Validation", f"{len(e)} erreur(s). Exporter quand même ?"): return
        p = filedialog.asksaveasfilename(title="Exporter DACTOR", defaultextension=".dactor", initialfile=self.actor["nom"] + ".dactor", filetypes=[("DMS Actor", "*.dactor")])
        if p:
            export_dactor(p, self.actor, Path(self.project).parent if self.project else None); compiled = materialize_actions(self.actor); self.status.configure(text=f"Exporté : {Path(p).name}"); messagebox.showinfo("DACTOR", f"{len(self.actor['etats'])} états • {len(compiled['transitions'])} transitions • {len(self.actor['actions'])} actions")
    def bundle(self):
        self.sync(); e,w=validate(self.actor)
        if e and not messagebox.askyesno("Validation",f"{len(e)} erreur(s). Exporter le bundle quand même ?"):return
        folder = filedialog.askdirectory(title="Bundle GDK")
        if not folder:return
        try:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", self.actor["nom"].lower()).strip("_") or "actor"; p = Path(folder) / (safe + ".dactor"); export_dactor(p, self.actor, Path(self.project).parent if self.project else None); (Path(folder) / (safe + "_handoff.txt")).write_text("DMS-GDK ACTOR HANDOFF\n=====================\n\nActor Builder V1.1 : actions auteur, mouvement X/Y runtime, aperçus DRES et transitions générées.\n\nLes séquences de touches temporisées, nage/grimpe/vol spécialisés et variables personnalisées restent des extensions explicites du gameplay.\n", encoding="utf-8"); self.status.configure(text="Bundle GDK exporté.")
        except Exception as ex:messagebox.showerror("Bundle GDK",str(ex))


if __name__ == "__main__":
    App().mainloop()
