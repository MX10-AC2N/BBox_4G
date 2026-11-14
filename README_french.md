# Bbox 4G Bouygues pour Home Assistant

🇬🇧 [English version](README.md)

Intégration personnalisée (HACS) pour les box Bouygues Telecom : voir **qui est connecté**,
**couper le Wi-Fi** et **bloquer un appareil**, en local, sans cloud ni dépendance Python.

> ⚠️ **État du projet** : développé et testé sur une **Huawei B528s (Bbox 4G)**.
> Le support des Bbox Sagemcom (fibre/ADSL) est présent dans le code mais **non testé** sur du matériel réel.
> Le blocage par filtre MAC modifie la configuration du routeur : essayez d'abord sur un appareil sans importance.

## Installation (HACS)

1. HACS → menu ⋮ → **Dépôts personnalisés**
2. URL : `https://github.com/MX10-AC2N/BBox_4G` — catégorie **Intégration**
3. Installer **Bbox Bouygues**, redémarrer Home Assistant
4. **Paramètres → Appareils et services → Ajouter une intégration → Bbox Bouygues**

| Champ | Valeur |
|---|---|
| Adresse de la box | `192.168.8.1` (4G, souvent) ou `192.168.1.1` (5G) ; `192.168.1.254` (fibre) |
| Identifiant | `admin` |
| Mot de passe | mot de passe d'administration de la box (étiquette), pas celui du Wi-Fi |

Utilisez l'**adresse IP** : `mabbox.bytel.fr` ne se résout pas toujours depuis un conteneur.

## Entités

**La box (appareil « Bbox »)**

| Entité | Rôle |
|---|---|
| `sensor.bbox_clients_connectes` / `sensor.bbox_appareils_connus` | nombre d'appareils connectés / connus |
| `sensor.bbox_reseau_invite` | état du réseau invité |
| `switch.bbox_wifi`, `…_24_ghz`, `…_5_ghz` | Wi-Fi global et par bande |
| `button.bbox_actualiser`, `button.bbox_reconnecter` | forcer une lecture / rouvrir la session |

**Chaque client connu** crée des entités sous la Bbox (par défaut) ou sur son propre appareil :

| Entité | Rôle |
|---|---|
| Présence / Suivi | connecté / déconnecté (`binary_sensor` + `device_tracker`) |
| Accès Internet | coupe la sortie Internet (filtre MAC) |
| Wifi | autorise / refuse l'appareil sur le Wi-Fi (liste noire du SSID) |
| Connexion | blocage selon la **portée** choisie dans les options |
| Réveiller | Wake-on-LAN |
| Appareil indépendant | déplace le client vers son propre appareil Home Assistant |

En mode **under_box** (défaut), les noms d'entités incluent le nom du client  
(ex. `Présence – iPhone`, `Accès Internet – NAS`) pour rester lisibles sous le seul appareil Bbox.

## Options

*Paramètres → Appareils et services → Bbox Bouygues → Configurer*

| Option | Défaut | Rôle |
|---|---|---|
| Fréquence d'interrogation | 60 s | délai entre deux lectures (15–600 s) |
| Portée du blocage | `lan` | `lan` filtre MAC · `wifi` liste noire du SSID · `internet` · `all` |
| Mode d'appareils | `under_box` | `under_box` = tout sous la Bbox · `separate` = un appareil HA par client |
| Tableaux de bord | (aucun) | cocher **Native** / **Auto-entities** / **Mushroom** pour les créer dans la barre latérale |

## Tableaux de bord (optionnel)

1. **Configurer** l'intégration → sélectionner un ou plusieurs tableaux de bord → enregistrer  
2. **Rafraîchir le navigateur** (ou redémarrer Home Assistant)  
3. Le tableau de bord apparaît dans la barre latérale

| Variante | Prérequis |
|---|---|
| **Native** | aucun |
| **Auto-entities** | carte HACS *auto-entities* |
| **Mushroom** | HACS *Mushroom* + *auto-entities* |

Ou via le service :

```yaml
action: bbox_4g.create_dashboard
data:
  dashboards:
    - auto
    - mushroom
```

## Contrôle parental

Regroupez les appareils d'un enfant (téléphone, tablette, console…) dans un **profil**. Les plages de coupure et la dérogation sont communes au profil ; le quota se règle appareil par appareil. Les règles sont appliquées par l'intégration elle-même, avec le filtre MAC de la box.

*Paramètres → Appareils et services → Bbox Bouygues → Configurer → Contrôle parental : ajouter un profil*

Chaque profil crée un appareil **Contrôle parental – <nom>** avec :

| Entité | Rôle |
|---|---|
| Interrupteur **Activé** | active ou désactive les règles (un nouveau profil démarre **désactivé**) |
| Heures **Coupure semaine / week-end – début / fin** | plages de coupure, possibles à cheval sur minuit (21:00 → 07:00). Début = fin : pas de plage |
| Nombres **Quota semaine / week-end – <appareil>** | quota journalier en minutes **par appareil** (chaque appareil du profil a le sien), `0` = pas de quota |
| Capteur **Statut** | `Libre`, `Coupé jusqu'à 07:00`, `Quota atteint`, `Dérogation jusqu'à 18:30`, `Désactivé` |
| Capteurs **Temps restant – <appareil>** | temps restant aujourd'hui : `1 h 25`, `Épuisé` ou `Illimité` (pas de quota) |
| Capteurs **Temps utilisé – <appareil>** | minutes comptées aujourd'hui pour chaque appareil |
| Bouton **Rétablir 1 h** | lève toutes les restrictions du profil pendant une heure |
| Interrupteur **Mode vacances** | applique la plage et le quota *week-end* tous les jours |
| Nombre **Alerte avant fin de quota** | minutes avant la fin d'un quota où l'alerte se déclenche (`0` = désactivée) |
| Boutons **+15 min de quota – <appareil>** | ajoute 15 minutes au quota du jour de cet appareil (fin à minuit) |
| Boutons **Rétablir 1 h – <appareil>** | lève les restrictions de cet appareil seulement, pendant une heure |
| Événement **Alertes** | `quota_warning`, `quota_reached`, `bedtime_start`, `bedtime_end`, `override_started` (voir plus bas) |

Fonctionnement :

- Les nuits qui **commencent le vendredi et le samedi** utilisent la plage *week-end* ; les autres, la plage *semaine*. Le quota « week-end » s'applique le samedi et le dimanche.
- Le quota est **par appareil** : chaque appareil a sa limite et son compteur, qui tourne tant qu'il est **connecté et autorisé**. Quand un appareil atteint son quota, seul celui-là est coupé, les autres continuent. Un téléphone qui reste connecté en Wi-Fi sans servir continue de compter : la box ne sait pas si l'écran est utilisé.
- Priorité : interrupteur désactivé → dérogation → plage de coupure → quota. Le compteur repart de zéro à minuit.
- Les règles sont vérifiées chaque minute et après chaque actualisation : **Home Assistant doit tourner** ; à l'arrêt, la box garde le dernier état.
- L'intégration ne libère que les appareils **qu'elle a bloqués elle-même** : un appareil bloqué à la main reste bloqué. Si vous débloquez un appareil pendant une restriction, il est rebloqué dans la minute : utilisez **Rétablir 1 h**.
- Les appareils doivent garder une adresse MAC fixe (désactivez l'option « adresse Wi-Fi privée / aléatoire » sur les téléphones des enfants).

Service : `bbox_4g.grant_extra_time` (`profil` → champ `profile`, `minutes` — 60 par défaut, `0` annule).

### Alertes et statistiques

- L'entité événement **Alertes** de chaque profil déclenche `quota_warning` (avec les minutes restantes), `quota_reached`, `bedtime_start`, `bedtime_end` et `override_started`. Utilisez-la comme déclencheur d'automatisation ; le nom de l'appareil, la MAC et les minutes sont dans les données de l'événement.
- La box a une entité événement **Nouvel appareil** (`new_device`) déclenchée quand un appareil jamais vu rejoint le réseau (nom, MAC, IP, liaison). Les appareils déjà connus ne sont pas signalés au premier démarrage.
- Les capteurs **Temps utilisé** alimentent les statistiques à long terme de Home Assistant : historique par jour, semaine ou mois.

```yaml
automation:
  - alias: Quota de Léa bientôt fini
    triggers:
      - trigger: state
        entity_id: event.controle_parental_lea_alertes
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'quota_warning' }}"
    actions:
      - action: notify.mobile_app_mon_telephone
        data:
          message: "{{ trigger.to_state.attributes.device }} : {{ trigger.to_state.attributes.remaining_min }} min restantes"
```

### Autres fonctions

- **Renommer des appareils** : *Configurer → Renommer des appareils*. Le nom remplace celui de la box dans les entités, tableaux de bord et alertes (vide = nom de la box).
- **Minuteur Wi-Fi** `bbox_4g.wifi_timer` (`band` : `all`, `24`, `5`, `guest` ; `minutes`) : allume une bande maintenant et l'éteint après le délai ; survit à un redémarrage ; `0` annule. Le réseau invité ne fonctionne que si la box l'expose (pas sur tous les modèles Huawei : une erreur claire est renvoyée sinon).
- Services `bbox_4g.add_quota_time` (`profile`, `device` facultatif, `minutes`) et `bbox_4g.grant_extra_time` (`device` facultatif).

## Services

| Service | Arguments |
|---|---|
| `bbox_4g.refresh` | — |
| `bbox_4g.block_device` / `bbox_4g.unblock_device` | `mac` |
| `bbox_4g.set_wifi` | `band` (`all`, `24`, `5`), `enabled` |
| `bbox_4g.set_internet_access` | `mac`, `allowed` |
| `bbox_4g.set_wifi_access` | `mac`, `allowed` |
| `bbox_4g.wake_device` | `mac` |
| `bbox_4g.grant_extra_time` | `profile`, `device` (facultatif), `minutes` |
| `bbox_4g.add_quota_time` | `profile`, `device` (facultatif), `minutes` |
| `bbox_4g.wifi_timer` | `band`, `minutes` |
| `bbox_4g.create_dashboard` | `dashboards` (`native`, `auto`, `mushroom`) |

```yaml
action: bbox_4g.block_device
data:
  mac: aa:bb:cc:dd:ee:ff
```

## À savoir

- **Une seule session d'administration** : les box Huawei n'en acceptent qu'une. Si l'interface web de la box est ouverte, la connexion de Home Assistant peut être refusée (code `108003`) et inversement. Fermez l'interface web ou utilisez le bouton **Reconnecter**.
- **Mot de passe refusé** : Home Assistant affiche une notification « Corriger » pour le ressaisir.
- **Filtre MAC en liste blanche** : si la box est en politique « liste blanche » avec des appareils autorisés, l'intégration refuse de bloquer pour ne pas couper Internet à tous.
- **Certificat TLS** : auto-signé, non vérifié par défaut (comportement habituel des box Bouygues).

## Dépannage

| Symptôme | Piste |
|---|---|
| « Impossible de joindre la box » | vérifier l'IP, que HA est sur le même réseau que la box, et qu'aucune session web n'est ouverte |
| « Identifiants refusés » | mot de passe **admin** de la box ; la box verrouille le compte après plusieurs essais |
| Entités « indisponibles » | fermer l'interface web de la box, puis **Reconnecter** |
| Dashboard absent de la barre latérale | rafraîchir le navigateur ou redémarrer HA après avoir coché les dashboards dans les options |
| Journaux | `docker logs homeassistant \| grep -i bbox` |

## Outil de diagnostic (facultatif)

`tools/bbox_cli.py` est un script indépendant (non utilisé par Home Assistant) pour tester la box depuis un terminal :

```bash
python3 tools/bbox_cli.py --host 192.168.8.1 --password '***' diag
python3 tools/bbox_cli.py --host 192.168.8.1 --password '***' host-table
```

## Crédits et genèse du projet

Cette intégration a été écrite **avec l'aide d'assistants IA**, sous la direction de son auteur, **MX10-AC2N**, qui a fixé les objectifs, l'a utilisée sur son propre routeur et son Home Assistant, et a décidé de ce qui était conservé :

- **MiniMax** — premières versions de l'intégration et du client de la box.
- **Claude** (Anthropic) — relectures et corrections du client de la box, packaging HACS et mise en place du dépôt, documentation française et anglaise, tableaux de bord, contrôle parental (profils, plages de coupure, quotas, alertes), minuteur Wi-Fi, renommage des appareils et tests automatisés.
- **Grok** — une première proposition de tableau de bord, relue puis remplacée par le code actuel.

Ce que cela implique pour vous :

- Le comportement sur matériel réel n'a été vérifié qu'avec un **Huawei B528s** (le routeur de l'auteur). Le support des Bbox Sagemcom n'est pas validé.
- Le **contrôle parental** (v2.2 et suivantes) est récent. Il est couvert par des tests automatiques exécutés sur un **routeur simulé** (`tests/`, lancés à chaque envoi), pas encore par un long usage réel.
- Du code écrit par des IA peut contenir des erreurs : [ouvrez une issue](https://github.com/MX10-AC2N/BBox_4G/issues) si quelque chose se comporte mal.

## Licence

MIT — voir [LICENSE](LICENSE). Projet non affilié à Bouygues Telecom.
