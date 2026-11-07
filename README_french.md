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

## Services

| Service | Arguments |
|---|---|
| `bbox_4g.refresh` | — |
| `bbox_4g.block_device` / `bbox_4g.unblock_device` | `mac` |
| `bbox_4g.set_wifi` | `band` (`all`, `24`, `5`), `enabled` |
| `bbox_4g.set_internet_access` | `mac`, `allowed` |
| `bbox_4g.set_wifi_access` | `mac`, `allowed` |
| `bbox_4g.wake_device` | `mac` |
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

## Licence

MIT — voir [LICENSE](LICENSE). Projet non affilié à Bouygues Telecom.
