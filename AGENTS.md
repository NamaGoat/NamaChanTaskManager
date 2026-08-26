# Projet : NamaChan Account Manager

Gestionnaire de comptes Roblox en Python avec interface CustomTkinter.
Ancien nom : "RobloxTaskManager". Le projet a été déplacé ici depuis
`C:\Users\namaz\PycharmProjects\IAtest\RobloxTaskManager` — ne pas toucher
à l'ancien dossier.

NB : `JOURNAL.md` raconte en français lisible l'historique des bugs et de
leur résolution (pour relecture humaine) — le tenir à jour à chaque bug
majeur.

## Architecture

- `app_ui.py` : UI principale (CustomTkinter). Vues accessibles via `self.views`
  + `show_view()` : comptes, jouer, multi, recents, features, logs, settings.
- `accounts.py` : stockage des tokens Roblox chiffrés en DPAPI (`accounts.json`),
  récupération du ticket d'auth via CDP (Chrome DevTools Protocol),
  extraction du cookie `.ROBLOSECURITY` depuis les profils Chrome,
  construction des URIs `roblox-player://` (`build_launch_uri`).
- `core.py` : multi-instance Roblox (fermeture du mutex single-instance via
  handles NtQuerySystemInformation), cap FPS FastFlags, priorité processus,
  suspend/resume, lancement multiple.
- `features.py` : AntiAFK et AutoRejoin.
- `main.py` : ANCIENNE version tkinter, référence uniquement — ne pas développer dessus.
- `dist\` : exes PyInstaller (`NamaChanAccountManager.exe` = build actuel).
- Spec PyInstaller : `NamaChanAccountManager.spec`.
- `test_*.py` : scripts de test jetables lancés directement avec Python.

## État / historique récent

- Ticket d'auth via CDP mis au point et fonctionnel
  (`get_ticket_via_cdp`, itérations `test_probe2.py` -> `test_probe6.py`,
  puis `test_ticket.py`, `test_launch.py`, `test_home.py`).
- Lancement du client avec ticket OK (variantes URI `launchmode:app/login`),
  build exe refait le 22/08.
- 23/08 : multi-instance RÉPARÉ et testé OK (3+ instances parallèles).
  Bugs corrigés dans `core.py` :
  1. `get_instances()` : `global _proc_cache` manquant -> tableau live mort.
  2. Restypes NTSTATUS signés (`c_long`) -> comparaisons `STATUS_INFO_LENGTH_MISMATCH`
     toujours fausses -> énumération de handles jamais retentée.
  3. Layout moderne de `SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX` sur Win11 24H2+ :
     buffer = en-tête 24 octets + entrées de 40 avec champs RÉORDONNÉS
     `[PID u64][Handle u64][Access u32][CBTI u16][TypeIdx u16][Object u64][Res u32]`.
  4. Roblox n'utilise plus `ROBLOX_SINGLEINSTANCE` mais `ROBLOX_singletonMutex`,
     `ROBLOX_singletonEvent`, `<chemin exe>.mtx` et `<chemin exe>.shm`
     (IPC "NoReload/warm start") -> strip Mutant+Event+Section, matching élargi.
  5. `launch_instances()` : les instances en boot créent leurs objets
     progressivement -> il faut stripper en boucle (~4 s) après CHAQUE spawn,
     pas seulement avant. `unlock_all()` ferme les objets de TOUTES les
     instances existantes avant chaque lancement.
- Test auto : `test_multi.py` (lance 2 instances, vérifie survie, nettoie).
- Suite (23/08) : `api_launch` (lancement par compte) strip aussi les objets
  singleton de TOUTES les instances avant chaque spawn. Vue "Multi Roblox" simplifiée :
  plus de bouton "Lancer instance(s)" ni compteur -> interrupteur ON/OFF
  "Multi-instance" (= `force_mutex` dans settings.json, persisté direct).
  Le multi s'active donc AVANT de lancer les comptes depuis la vue Comptes.
- IMPORTANT (23/08, après tests réels) : NE JAMAIS stripper les processus
  Roblox jeunes (< 8 s) ni pendant leur boot -> `unlock_all(min_age=8.0)`.
  Le strip continu pendant le boot casse le bootstrap Roblox (processus
  updateur aussi nommé RobloxPlayerBeta.exe) -> boucle kill/respawn qui
  ressemble à une réinstallation en boucle et tue les instances existantes.
  Log complet du cycle de vie des instances dans la Console
  (`refresh_table_loop` : diff des PIDs toutes les 2 s).
- 23/08 suite : multi validé OK par l'utilisateur (2 comptes parallèles).
  Détection des processus fantômes arrière-plan (`pid_has_visible_window`
  via EnumWindows) -> statut "Arrière-plan" dans le tableau au lieu de
  "En jeu/app". Roblox laisse des stubs ~100 MB sans fenêtre après fermeture.
  NB : relancer un compte déjà connecté ailleurs déconnecte l'ancienne
  session (comportement Roblox normal, pas un bug du multi).
- 23/08 SOIR - BUG MAJEUR (mise à jour Roblox de 18:51/18:54, versions
  ddf602d9/2f3eb5f) : la fermeture PROPRE d'une instance (clic sur X)
  signale les autres instances via objets kernel nommés (Event/Mutant,
  recréés en continu par Roblox) -> TOUTES les autres se ferment en ~4 s
  (teardown propre loggé "SessionTransitionFSM Tearing down") + fantômes.
  Un kill BRUTAL ne propage PAS. Reproduit et diagnostiqué avec
  test_close_prop / test_hardkill / test_guardian.
  FIX : gardien dans core.py (`start_guardian()` / `stop_guardian()`) :
  thread daemon qui re-strippe les objets single-instance de TOUTES les
  instances >8 s toutes les 0,5 s tant que force_mutex=ON. Branché sur le
  switch Multi (`_sync_guardian` dans apply_feature_settings + destroy).
  Validé par test_guardian.py : B survit à la fermeture propre de A.
  NB : le guardian respecte min_age=8 s (jamais sur les jeunes en boot).
- 24/08 - Cap FPS perdu après MAJ Roblox : `apply_fps_cap()` écrit dans
  `Versions\version-XXXX\ClientSettings\ClientAppSettings.json` ; une MAJ
  crée un NOUVEAU version-* (l'ancien est supprimé) -> le fichier disparaît,
  retour à 30/60. `fps_default` était bien en settings.json mais jamais
  réappliqué auto. FIX : `core.ensure_fps_cap()` (compare le flag
  DFIntTaskSchedulerTargetFps de chaque version, réécrit seulement si besoin)
  appelé au démarrage de l'app + avant chaque `api_launch` (avant Popen).
- 25/08 - Qualité graphique repassant en "Automatique" en multi : PAS un bug
  d'écriture NamaChan (l'app ne posait aucun flag qualité). Le mode
  Auto/Manuel vit dans `%LOCALAPPDATA%\Roblox\rbx-storage` (LevelDB partagée
  par toutes les instances) -> écritures concurrentes à la fermeture =
  dernier écrivain gagne, retour à Auto. FIX : forçage FastFlags ->
  `apply_fps_cap(fps, gfx_mode)` écrit/retire aussi
  `DFIntDebugFRMQualityLevelOverride` (perf=1, equilibre=8, pro=21 ;
  GFX_QUALITY_LEVELS/GFX_LABELS dans core.py). Vue Multi : menu "Qualité :"
  (Auto/Perf/Équilibré/Pro) à côté du cap FPS, persisté
  `settings.json["gfx_quality"]` (+ clé remise dans apply_feature_settings).
  ensure_fps_cap vérifie désormais FPS + flag qualité.
- 25/08 SUITE - QoL : (1) Join by player name -> `accounts.resolve_player()`
  (users API) + `accounts.get_player_presence()` (presence API, sans auth)
  -> target `{"mode":"job","place_id":...,"code":job_id}` réutilise le mode
  job de `build_launch_uri` = joint le MÊME serveur. UI : carte
  "REJOINDRE UN JOUEUR" (entry + bouton, Entrée OK) dans la vue Comptes,
  bouton désactivé pendant la résolution. Si presenceType != 2 -> message
  (pas en jeu); si gameId absent -> fallback serveur public.
  (2) Historique jeux récents : grille 5 colonnes à trous remplacée par une
  liste compacte (`reload_recents` réécrite : rangées icône 30px + nom +
  "Place X · compte" + bouton ▶, helper `_fill_recent_icon`).
- 25/08 SUITE - FPS bloqués à 120 malgré le flag à 240 : Roblox a un réglage
  officiel "Maximum Frame Rate" (menu Échap) stocké dans
  `%LOCALAPPDATA%\Roblox\GlobalBasicSettings_13.xml`
  (`<int name="FramerateCap">120</int>`) qui ÉCRASE le FastFlag depuis la
  refonte caps fin 2025. FIX : `core.write_global_framerate_cap()` /
  `read_global_framerate_cap()` (regex ciblée sur la balise) ; appelé par
  apply_fps_cap et vérifié par ensure_fps_cap -> réaligné auto au démarrage/
  avant chaque lancement. NB : XML partagé entre instances -> ne s'applique
  qu'aux instances lancées après le changement.
- 25/08 SUITE - Mode Perf enrichi (demande user) : `GFX_PRESET_FLAGS["perf"]`
  ajoute FFlagDebugSkyGray=True, DFFlagTextureQualityOverrideEnabled=True +
  DFIntTextureQualityOverride=0 (textures mini), DFIntCSGLevelOfDetailSwitchingDistance*
  =100000 (render distance max). Auto/Équilibré/Pro : aucun extra ;
  GFX_MANAGED_KEYS garantit le retrait propre des extras quand on change de
  mode. Tooltips Qualité mis à jour (GFX_TOOLTIPS dans app_ui.py).
- 25/08 SUITE - Mode Perf++ ajouté (test user) : sur Blox Fruits le mode Perf
  laissait voir le vide -> le niveau FRM 1 rétrécit la zone rendue, les
  distances LOD seules ne suffisent pas. "perfplus" = FRM 10 (grande
  distance) MAIS textures forcées mini (override), ombres coupées
  (FIntRenderShadowIntensity=0 + DFFlagDebugPauseVoxelizer=True) et LOD x2
  (=200000). NB : FFlagDebugSkyGray semble ignoré par les clients récents
  (allowlist) -> ciel toujours pas gris chez l'utilisateur, à re-tester.
- 25/08 SUITE - Perf++ poussé au max (user : "toujours pas assez") :
  perfplus passe à FRM 21 (= Pro) + DFIntDebugRestrictGCDistance=500000
  (lève la restriction de distance de dessin). Si le vide persiste au loin,
  suspect = streaming du jeu (StreamingEnabled : Roblox n'envoie plus les
  données lointaines) -> non contournable par config client.
- 25/08 SUITE - VRAIE cause trouvée du "vision pas au max" en Perf++ vs
  settings manuels : le rayon de STREAMING suit le réglage utilisateur RÉEL
  dans GlobalBasicSettings_13.xml (`<token name="SavedQualityLevel">`,
  `<int name="GraphicsQualityLevel">`), PAS l'override FastFlag FRM. User
  avait SavedQualityLevel=0 (auto) -> serveur n'envoyait pas les îles
  lointaines malgré FRM 21. FIX : `write_global_quality_level(10)` /
  `read_global_quality_level()` -> appliqué par apply_fps_cap pour TOUS les
  modes forcés (tout sauf auto), vérifié par ensure_fps_cap. NB :
  SavedQualityLevel est un <token>, GraphicsQualityLevel un <int> (regex
  `<(?:int|token)>`). FIntCameraFarZPlane testé puis RETIRÉ (hors
  allowlist, ignoré silencieusement — aucun risque mais inutile).
- 25/08 SUITE - Bilan modes qualité (validé user : "je vois toute la map") :
  Perf = FRM 1 + textures mini + sky gris -> FPS max, vision complète ;
  Perf++ = FRM 21 + textures mini + ombres off -> vision complète ET
  détaillée au loin, plus gourmand ; Équilibré/Pro inchangés. Tous écrivent
  SavedQualityLevel/GraphicsQualityLevel=10 (vision/streaming max), Auto ne
  touche à rien. (26/08 : ombres off + voxelizer pause ajoutés AUSSI au mode
  Perf -> les deux modes sont identiques sauf le niveau moteur 1 vs 21.)
- 25/08 SOIR - INVERSION DES NOMS Perf / Perf++ (demande user : "++" = le
  plus perf). Simple permutation des LIBELLÉS, presets internes inchangés :
  GFX_LABELS -> "Perf++" = preset `perf` (FRM 1, FPS max absolu) et "Perf" =
  preset `perfplus` (FRM 21, vision détaillée au loin). Tooltips (GFX_TOOLTIPS)
  mis en cohérence. Ordre du menu : Auto / Perf++ / Perf / Équilibré / Pro.
  Settings sauvegardés restent valides (clés internes inchangées).
- 25/08 SOIR - Revue de code AntiAFK/AutoRejoin : intégration complète
  confirmée, roadmap [x]. Limite AutoRejoin documentée DANS l'UI (note sous
  le switch vue Features : switch ON = fermeture manuelle aussi relancée).
  Tests réels restants côté user : idle 20-30 min (AntiAFK), rejoin après
  fermeture manuelle (AutoRejoin).

## Liste de progression (roadmap)

1. [x] Base : gestion des comptes (tokens DPAPI, ticket via CDP, lancement)
2. [x] UI CustomTkinter avec sidebar + vues
3. [x] Ticket d'auth via CDP fonctionnel
4. [x] Multi Roblox / multi-instances (validé 23-25/08 : guardian anti-cascade,
       FPS cap réappliqué après MAJ, forçage qualité graphique Auto/Perf/Équilibré/Pro)
5. [x] AntiAFK : intégré (25/08 revue de code). Switch + intervalle persistés
       (`settings.json["anti_afk"]/["aa_interval"]`, min 15 s), démarré au boot
       si activé. Envoie VK_F13 (0x7C) via PostMessageW WM_KEYDOWN/KEYUP à
       toutes les fenêtres VISIBLES des instances (`get_roblox_windows`).
6. [x] AutoRejoin : intégré (25/08 revue de code). `api_launch` appelle
       `rejoin.track(pid, compte, target)` ; boucle 3 s détecte les PIDs morts,
       relance après délai avec la MÊME target (mode job = même serveur),
       le nouveau PID est re-tracké. NB/limite connue : ne distingue PAS
       fermeture volontaire vs déconnexion -> switch ON = toute instance
       fermée est relancée (note affichée dans la vue Features).
7. [ ] Donations : simple bouton de soutien (pas obligatoire, discret) dans
       l'UI -> lien Ko-fi OU lien de profil Roblox pour envoyer des Robux.
       À demander à l'utilisateur les liens exacts le moment venu.
8. [ ] **QoL diverses** <- ON EN EST LÀ :
   - [x] Join by player name : entrer un pseudo Roblox -> résolution
         users API + présence API -> joint le MÊME serveur (`mode: job`,
         déjà supporté par `build_launch_uri`). UI : carte "Rejoindre un
         joueur" dans la vue Comptes.
   - [x] Refonte historique jeux récents (25/08) : grille à trous remplacée
         par une liste compacte (icône + nom + place/compte + bouton ▶).
   - [ ] Autres QoL à définir avec l'utilisateur

### Détail Multi Roblox (étape 4)
Le code existe déjà :
- UI : `app_ui.py` -> `build_view_instances()` (vue "Multi Roblox") :
  lancer N instances, switch Force multi-instance, Kill sélection/TOUT,
  Suspendre/Reprendre, priorité High/Normal/Low, limite FPS, tableau live
  (PID, Compte, CPU, RAM, Statut, Uptime).
- Moteur : `core.py` -> `launch_instances()`, `close_single_instance_mutex()`,
  `get_instances()`, suspend/resume, priorité, FPS cap.

## Conventions

- Répondre en français.
- RÈGLE : à CHAQUE bug trouvé/diagnostiqué/corrigé -> documenter dans
  `AGENTS.md` (notes techniques, section État/historique) ET `JOURNAL.md`
  (récit lisible : symptôme -> enquête -> cause -> fix). Systématique,
  pas seulement les bugs "majeurs".
- Après chaque modif : tester en lançant `python app_ui.py`.
  NB : le bon interpréteur est
  `C:\Users\namaz\AppData\Local\Programs\Python\Python310\python.exe`
  (Python 3.14 système n'a PAS customtkinter ; `python` seul pointe vers le
  stub Microsoft Store).
- Rebuild exe : PyInstaller avec `NamaChanAccountManager.spec`, puis TOUJOURS
  copier `dist\NamaChanAccountManager.exe` sur le Bureau (l'utilisateur y
  lance l'exe) :
  `Copy-Item dist\NamaChanAccountManager.exe ([Environment]::GetFolderPath('Desktop')) -Force`
- NE JAMAIS éditer les fichiers sources via Get-Content/Set-Content PowerShell
  (double-encodage UTF-8 -> mojibake) : utiliser uniquement les outils
  d'édition dédiés.

## Thème / layout

- Système de THÈMES (23/08) : dict `THEMES` en haut d'`app_ui.py`
  (Miyabi cyan #41bccc par défaut, Écarlate, Violet, Ambre) + `load_theme()`.
  Sélecteur dans Paramètres (`opt_theme`) -> sauvegarde `settings.json["theme"]`
  + redémarrage auto (`_restart` + boucle `while True` du `__main__`).
  NB : `apply_feature_settings()` reconstruit tout le settings dict -> ne pas
  oublier d'y remettre la clé "theme" si on ajoute des réglages.
- Palette de base : BG/CARD/CARD2/FG/MUT fixes + ACCENT/ACCENT_H par thème.
  Vert menthe (Rejoindre) et rouges (Kill/Supprimer, sémantiques) identiques
  pour tous les thèmes. Changer les couleurs = éditer THEMES / constantes.
- Dégradés de fond (23/08) : `_bg_image()` dans `app_ui.py` génère un dégradé
  PIL (vertical sidebar, horizontal header) teinté par l'ACCENT du thème,
  posé via CTkLabel + place() SOUS les widgets (créé en premier).
  Image perso possible : poser `sidebar_bg.png` ou `.jpg` à côté de l'exe
  (APP_DIR) -> cover-crop et remplace le dégradé de la sidebar + header.
- Layout : SIDEBAR UNIQUEMENT. L'ancien layout "classique" (et ses vues
  Jouer/Récents séparées) a été SUPPRIMÉ le 23/08. La clé obsolète "layout"
  de settings.json est ignorée.

## Logo / icône

- Généré par `gen_icon.py` (PIL) : dégradé bleu->rose, "NC" + petit cœur ->
  `namachan.ico` (+ `namachan_preview.png` pour aperçu).
- Intégré : icône exe via `.spec` (`icon='namachan.ico'` + datas) et icône de
  fenêtre via `_apply_icon()` dans `app_ui.py` (fenêtre principale + popups).
- Si le logo change : relancer `gen_icon.py` puis rebuild.
