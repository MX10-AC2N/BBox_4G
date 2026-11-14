# Rapport de tests

Rapport de tests en anglais : [TEST_REPORT.md](TEST_REPORT.md)

✅ **Les 18 tests ont réussi**

| | |
|---|---|
| Date (UTC) | 2026-11-14 |
| Version de l'intégration | 2.3.0 |
| Home Assistant utilisé pour les tests | 2026.2.3 |
| Python | 3.13.18 |
| Durée | 2.8 s |

Ces tests s'exécutent automatiquement à chaque envoi ([GitHub Actions](https://github.com/MX10-AC2N/BBox_4G/actions/workflows/validate.yml)). Chacun démarre un vrai Home Assistant en mémoire, charge l'intégration et dialogue avec un **routeur simulé**, avec une horloge simulée pour vérifier en quelques secondes les nuits, les quotas et les minuteurs.

## Résumé

| Domaine | Réussis |
|---|---|
| Tableaux de bord | 3 / 3 |
| Contrôle parental, services et options | 15 / 15 |

## Ce qui est vérifié

### Tableaux de bord

| Résultat | Ce qui est vérifié | Test | Durée |
|:-:|---|---|--:|
| ✅ | Les trois tableaux de bord (natif, auto, Mushroom) contiennent la section de contrôle parental, ne citent que des entités qui existent, et chaque modèle s'affiche dans le vrai moteur de modèles de Home Assistant. | `test_dashboards` | 0.35 s |
| ✅ | Un appareil coupé n'apparaît que dans « Appareils bloqués », jamais aussi dans « Appareils connectés » ; les textes et compteurs des cartes s'affichent correctement. | `test_sections_are_exclusive_and_texts_render` | 0.08 s |
| ✅ | Le tableau « Temps restant aujourd'hui » de chaque profil s'affiche avec une ligne par appareil (« Illimité » quand il n'y a pas de quota). | `test_left_table_renders` | 0.05 s |

### Contrôle parental, services et options

| Résultat | Ce qui est vérifié | Test | Durée |
|:-:|---|---|--:|
| ✅ | Plages de coupure : une nuit à cheval sur minuit (21:00 → 07:00) est coupée aux bonnes heures ; les nuits du vendredi et du samedi utilisent la plage week-end ; début = fin désactive la plage ; les plages de journée fonctionnent. | `test_window_overnight_and_kinds` | 0.01 s |
| ✅ | Textes du temps restant : 85 minutes s'affichent « 1 h 25 », 45 « 45 min », sans quota « Illimité », quota épuisé « Épuisé ». | `test_format_helpers` | 0.01 s |
| ✅ | Priorité des règles : profil désactivé, puis dérogation, puis plage de coupure, puis quota ; le quota est par appareil avec sa valeur week-end ; une dérogation lève le quota. | `test_state_priority_and_quota_kind` | 0.01 s |
| ✅ | Un profil crée ses entités (interrupteur, heures, quotas, capteurs d'usage, boutons) et démarre désactivé (« Désactivé »). | `test_entities_created` | 0.04 s |
| ✅ | À l'heure de coupure, les appareils du profil sont coupés ; « Rétablir 1 h » lève la coupure puis elle se réapplique ; les appareils sont libérés le matin ; un appareil bloqué à la main reste bloqué. | `test_schedule_override_and_manual_block` | 0.27 s |
| ✅ | Le service grant_extra_time lève la restriction pour une durée donnée (nom du profil insensible à la casse), 0 minute l'annule, et un profil inconnu renvoie une erreur. | `test_service_grant` | 0.08 s |
| ✅ | Quota par appareil : seul le téléphone qui a un quota est coupé, le PC sans quota continue ; le compteur s'arrête quand l'appareil est coupé ; la dérogation lève le quota ; minuit remet tout à zéro. | `test_quota_is_per_device` | 0.46 s |
| ✅ | Supprimer un profil libère les appareils qu'il avait bloqués, mais pas ceux bloqués à la main. | `test_remove_profile_releases` | 0.08 s |
| ✅ | Options : création d'un profil (erreurs si nom vide ou aucun appareil), les options enregistrées conservent les autres réglages, un appareil ne peut pas être dans deux profils, et l'étape des réglages généraux conserve les profils. | `test_options_flow` | 0.06 s |
| ✅ | Alertes (quota bientôt fini, quota atteint, dérogation démarrée) déclenchées une seule fois ; le bonus +15 min et le service add_quota_time prolongent le quota du jour d'un appareil (erreur pour un appareil illimité) ; « Rétablir 1 h » fonctionne pour un seul appareil. | `test_alerts_bonus_and_device_override` | 0.40 s |
| ✅ | Mode vacances (calcul) : la plage et le quota du week-end s'appliquent tous les jours. | `test_holiday_mode` | 0.02 s |
| ✅ | Interrupteur du mode vacances : activé, un lundi soir est coupé à l'heure du week-end (22:00) et non à celle de semaine (21:00). | `test_holiday_switch_entity` | 0.28 s |
| ✅ | Un appareil jamais vu déclenche un événement « nouvel appareil » une seule fois (les appareils déjà connus au démarrage ne sont pas signalés) ; le capteur d'usage du jour est prévu pour les statistiques à long terme. | `test_new_device_and_statistics` | 0.05 s |
| ✅ | Renommer un appareil dans les options remplace le nom de la box partout ; un nom vide le rétablit. | `test_custom_names` | 0.06 s |
| ✅ | Minuteur Wi-Fi : la bande est allumée, éteinte après le délai, peut être annulée, et une bande non gérée par la box renvoie une erreur claire sans minuteur résiduel. | `test_wifi_timer` | 0.34 s |

## Ce que ces tests ne couvrent pas

- Le comportement d'un **vrai routeur** : connexion, filtrage MAC et commandes Wi-Fi sont simulés. Seul le Huawei B528s de l'auteur a été utilisé en réel ; les Bbox Sagemcom ne sont pas validées.
- L'**affichage dans un navigateur** : les tableaux de bord sont générés et leurs modèles sont calculés, mais les cartes (dont Mushroom et auto-entities) ne sont pas dessinées à l'écran.
- Le **temps réel** : l'horloge est simulée, donc les très longues durées (plusieurs jours de suite, redémarrages à un moment précis) ne sont couvertes qu'en partie.
