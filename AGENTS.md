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
- `updater.py` : auto-update depuis GitHub Releases (check version, download,
  remplacement via PowerShell, nettoyage fichiers orphelins).
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
- 26/08 - Updater "Security validation failure" réparé (v1.0.3 → v1.0.10).
  L'updaterPyInstaller rejetait le remplacement quand le téléchargement
  passait par un dossier temporaire. Fix : download dans le MÊME répertoire
  que l'exe + `cleanup_orphan_files()` au démarrage pour les fichiers
  `.new`/`.old` orphelins. Scripts batch → VBScript → PowerShell pour
  contourner la validation PyInstaller. Version parser révertée en 3 parties
  (4 parties cassait la comparaison). Release v1.0.10 OK sur GitHub.
- 26/08 SUITE - Modes Perf : render max + suppression ciel gris Perf.
  Perf++ (FRM 1) : LOD monté à 200000 + `DFIntDebugRestrictGCDistance=500000`
  (render distance max). Perf (FRM 21) : `FFlagDebugSkyGray` retiré (ciel
   normal, nuit visible). Les deux modes ont désormais textures mini, ombres
   off, render distance max. La nuit noire est un comportement normal Roblox.
- 26/08 SUITE - Allowlist FastFlags Roblox (depuis 29/09/2025) :
  seuls les flags de la liste blanche sont reconnus. Flags non-allowlistés
  testés et virés : `DFIntDebugRestrictGCDistance`, `FIntRenderShadowIntensity`,
  `FIntDebugTextureManagerSkipMips`, `DFIntPerformanceControlTextureQualityBestUtility`.
  Fix : `DFIntTextureQualityOverride` passé de 0 (auto) à 1 (mini forcé).
  Nouveau mode **Perf Render Max** (FRM 21 + quality min + render max) pour
  les cartes AMD qui coupent le streaming avec FRM 1. Diff AMD vs NVIDIA
  constatée : NVIDIA gère FRM 1 avec vision complète, AMD non.
  Ordre menu : Auto / Perf++ / Perf / Perf Render Max / Équilibré / Pro.
- 26/08 SUITE - Convention : ne jamais push GitHub sans demander à
  l'utilisateur de tester l'exe sur PC d'abord. Réduire le nombre de releases.
- 26/08 SUITE - Nettoyage + boost modes Perf (allowlist officielle 18 flags,
  inchangée en 2026).
  RETIRÉS (hors allowlist, ignorés silencieusement) : `FIntRenderShadowIntensity`
  (ombres off), `DFIntDebugRestrictGCDistance` (render distance). Ils ne faisaient
  plus aucun effet.
  AJOUTÉS aux 3 modes (perf/perfplus/perfrendermax), tous allowlistés :
  `FIntDebugForceMSAASamples=-1` (anti-aliasing off), `FIntFRMMinGrassDistance=0`
  + `FIntFRMMaxGrassDistance=0` (herbe off). Le seul levier "ombres douces" restant
  est `DFFlagDebugPauseVoxelizer` (déjà présent).
  Rappel des flags allowlistés non utilisés restants : backend graphique
  (`FFlagDebugGraphicsPreferD3D11/Vulkan/OpenGL`), `FFlagHandleAltEnterFullscreenManually`.
- 26/08 SUITE - BUG UPDATER (v1.0.16->17, signalé user : download OK mais
  remplacement échoué, restait en v16) : `updater.download_update()` téléchargeait
  dans `tempfile.gettempdir()` (dossier temp système) puis `apply_update` copiait
  le fichier temp vers l'exe. La validation PyInstaller/antivirus rejetait le
  remplacement depuis un dossier temporaire -> l'app restait sur l'ancienne version.
  FIX : `download_update()` télécharge désormais `namachan_update.new` dans le
  MÊME répertoire que l'exe (`os.path.dirname(sys.argv[0])`, ex. le Bureau) + copie
  sur place. C'était le "fix" documenté v1.0.10 qui avait été perdu/régressé.
  `cleanup_old_files()` nettoie déjà `namachan_update.*` au démarrage.
- 26/08 SUITE - Diagnostiqué (v1.0.19 ne s'appliquait pas, restait en 0.18) :
  le download fonctionne (fait dans le process avant exit) mais le script
  PowerShell lancé en `DETACHED_PROCESS` était TUÉ quand le parent PyInstaller
  onefile faisait `os._exit(0)` (le job object onefile tue les enfants à la
  mort du parent). Résultat : ni rename ni copy, `.new` nettoyé au démarrage
  suivant par `cleanup_old_files()` -> "il ne reste rien" + toujours l'ancienne
  version.
  FIX (v1.0.20) : ajout de `CREATE_BREAKAWAY_FROM_JOB` (`0x01000000`) aux
  creationflags du Popen pour que le PowerShell survive à la mort du parent.
- 26/08 SUITE - FIX DÉFINITIF UPDATER (v1.0.22) : le BREAKAWAY_FROM_JOB seul
  ne suffisait pas (le `.new` restait, version toujours inchangée). Cause
  racine probable : le script PowerShell détaché était tué/non-exécuté
  (encodage UTF-8 sans BOM du ps1 lu en ANSI par PS 5.1 -> chemin corrompu si
  caractères non-ASCII, + job object onefile qui tue les enfants à la mort du
  parent). NOUVELLE APPROCHE : `apply_update()` ne passe PLUS par PowerShell.
  Le remplacement se fait en PUR PYTHON, de façon synchrone dans le process,
  avant `os._exit(0)` : `os.rename(exe→.old)` (autorisé sur Windows pour un
  exe en cours), `shutil.copy2(.new→exe)`, relance du nouveau en
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | 0x01000000`, puis exit.
  Plus aucun sous-processus PowerShell à maintenir en vie. `import tempfile`
  supprimé, `import shutil` ajouté.
- 26/08 SUITE - Popup "Security validation failure / failed to obtain exe path
  for parent process" au moment de l'update : causé par la RELANCE AUTOMATIQUE
  du nouvel exe après le remplacement. FIX (v1.0.24) : `apply_update()` ne
  relance PLUS le nouveau exe automatiquement. Il remplace le fichier puis
  `os._exit(0)`, et l'UI demande à l'utilisateur de relancer l'app manuellement
  ("Mise à jour installée ! Relance l'app."). Plus de process enfant relancé
  => plus de popup PyInstaller.
- 26/08 SUITE - UI : détail modes qualité + confirmation en double-clic.
  (1) Tooltips qualité enrichis (`GFX_TOOLTIPS` dans app_ui.py) : chaque mode
  liste désormais exactement tout ce qu'il fait (FRM, textures, AA off, herbe off,
  ombres douces, ciel) + bouton "ℹ" à côté du menu Qualité (vue Multi) qui affiche
  le même détail au survol — sans ouvrir de nouvelle fenêtre.
  (2) Kill sélection / Kill TOUT : confirmation en double-clic sans popup
  (`_armed_kill` / `_disarm`) : 1er clic -> le bouton passe en "Confirmer ?",
  2e clic dans 2,5 s -> exécute. Le `messagebox.askyesno` de kill_all retiré.
- 26/08 SUITE - Diagnostic perf sur PC portable entry-level (ami user) :
  i5-10300H (4C/8T, laptop 2020) + GTX 1650 mobile 4GB -> aucun gain
  visible des modes Perf. Cause : Roblox est CPU-bound, le CPU throttle
  thermiquement sur laptop (TDP plafonné, 90-95°C), et les flags qualité
  touchent le GPU qui n'est déjà pas le bottleneck. Les modes Perf ne
  peuvent pas améliorer un CPU qui plafonne. Recommandations données :
  limiter FPS à 60 sur laptop, vérifier que le GPU dédié est bien activé
  dans les paramètres Windows (batterie -> perf élevée).
- 08/09 - Fix "Join by player" + nouvelle feature "Amis en ligne".
  (1) BUG RACINE TROUVÉ : l'API présence Roblox renvoie le champ
  `userPresenceType` (PAS `presenceType` avec un "Type"). L'ancien code
  lisait `presenceType` -> TOUJOURS absent -> tout le monde classé
  "offline" -> join by player disait "introuvable"/"pas en jeu" même quand
  le joueur était en jeu (ex. Blox Fruits), et la liste d'amis ne
  montrait jamais personne en ligne. FIX : parsing de
  `userPresenceType` (avec `presenceType` en secours). Valeurs : 0=offline,
  1=online, 2=in_game (avec placeId, gameId, lastLocation).
  (2) AUTH AJOUTÉE en bonus : `get_player_presence(id, token=)` et
  `get_users_presence(user_ids, token=)` (batch jusqu'à ~100 IDs -> dict
  {user_id: {status, place_id, job_id, last}}) passent le `.ROBLOSECURITY`
  du compte sélectionné pour obtenir la présence fiable (le statut de
  présence peut être masqué sans auth selon la confidentialité du joueur).
  `get_player_presence` est un wrapper du batch (user unique ou None).
  (3) NOUVEAU `accounts.get_friends(user_id, token)` : l'endpoint
  `friends.roblox.com/v1/my/friends` n'existe PLUS (404) -> utilisé
  `friends.roblox.com/v1/users/{user_id}/friends` (pagination limit=100).
  Attention : cet endpoint renvoie les IDs AVEC name/displayName VIDES ->
  les noms sont récupérés via le batch `users.roblox.com/v1/users` (POST,
  `_get_users_batch`, retry sur 429, lots de 100). Retour : liste
  [{id, name, display}].
  (4) UI vue Comptes : carte "AMIS EN LIGNE" (self.friends_box scrollable)
  sous "Rejoindre un joueur" : bouton "⟳ Charger" -> charge les amis du
  compte sélectionné + leur présence batch -> liste des amis en ligne
  (🟢 online / 🎮 en jeu) avec bouton ▶ pour rejoindre (même serveur si
  gameId, sinon serveur public). Compteur dans le header. DisplayName
  affiché, pseudo entre parenthèses si différent.
  (5) UI COMPACTÉE : la carte "Choisir un jeu" a été réduite (aperçu 64px
  au lieu de 120, bouton Rejoindre 36px + moins de texte) et le panneau
  droit de la vue Comptes est maintenant un CTkScrollableFrame -> la
  section "Jeux récents" (qui dépassait l'écran => "j'ai supprimé les
  old games") réapparaît. rec_grid en hauteur fixe (expand retiré).
  PUIS (08/09) LAYOUT 2 COLONNES : "Amis en ligne" et "Jeux récents"
  placés CÔTE À CÔTE (fcard à gauche, rcard à droite, mêmes hauteurs
  250px) -> plus besoin de scroll vertical pour voir les jeux récents.
  Choisir un jeu + Rejoindre un joueur restent au-dessus sur toute la
  largeur.
  PUIS (08/09) ONGLET UNIQUE : les 2 cartes côté à côte remplacées par un
  CTkTabview (onglets "AMIS EN LIGNE" / "JEUX RÉCENTS"), hauteur 230.
  AMIS AUTO-H24 : `load_friends(auto=True)` est appelé à l'ouverture de la
  vue Comptes (`show_view`) + à chaque `select_account` + toutes les 60 s
  (boucle `friends_refresh_loop`, seulement si vue Comptes affichée).
  Anti-rafale : skip si dernier load < 3 s (`_friends_last_load`).
  `_friends_thread` sécurisé : try/except globale + logs `[Amis] ...`
  (jamais de bouton bloqué ni d'exception silencieuse). Backoff 429 du
  batch users réduit (1.5 s, 3 essais max).
  (6) ROBUSTESSE join by player : `resolve_player` loggue le code HTTP
  (`_last_resolve_status`) ; le fallback par liste d'amis du compte se
  fait TOUJOURS quand `resolve_player` échoue (même sur 429 rate-limit
  de l'endpoint usernames) : match sur name/displayName insensible à la
  casse ; 429 sans match -> "API Roblox saturée".
  NB : développé en SOURCE local uniquement (pas encore push GitHub/
  Bureau/exe). Testé avec les vrais comptes en local : 52 amis récupérés,
  2 en ligne détectés in_game. Interpréteur requis Python310.
- 08/09 SUITE (3) - TÊTES DES AMIS EN LIGNE + ROBUSTESSE ~300 amis :
  (1) La rangée d'un ami en ligne affiche désormais la TÊTE (avatar
  headshot 32px, batch thumbnail `avatar-headshot?userIds=a,b,c...` par
  lots de 100, `_avatars_map` dans app_ui.py) à gauche du nom, chargée en
  arrière-plan après le rendu (nom + status apparaissent immédiatement, le
  visage arrive ensuite). Images téléchargées en threads parallèles,
  appliquées via self.ui().
  (2) `accounts._get_users_batch` PARALLÉLISÉ (threads, un par lot de 100,
  timeout 8s, backoff 1s) : 312 IDs passent de 14.7s à 0.2s (le goulot
  était les 3 requêtes POST séquentielles, pas le rate-limit).
  (3) `accounts.get_users_presence` paginé en lots de 100 aussi (threads) :
  avant, un POST unique avec 300 IDs risquait 429/400. Retour identique
  ({user_id: {status, place_id, job_id, last}}), les appels existants
  inchangés. Testé : 52 presences en 0.2s.
  À re-tester côté user sur le compte principal (~300 amis).
- 08/09 SOIR - BUGC PSEUDOS DES AMIS (affichés en ID au lieu du pseudo) :
  l'endpoint POST `users.roblox.com/v1/users` (batch) est TOUJOURS en 429
  (rate-limit Roblox, même avec le cookie .ROBLOSECURITY) -> `_get_users_batch`
  retournait vide -> le fallback affichait l'ID à la place du pseudo (lignes
  344-345 de get_friends). Le GET `/v1/users/{id}` individuel, lui, marche.
  FIX : (1) nouveau `accounts._get_users_individual(ids, token)` : résout les
  pseudos par GET individuel un par un, en parallèle (semaphore 10, retry 429
  backoff 0.6s, 404/400 ignorés) — fiable même en rate-limit.
  (2) `get_friends(..., resolve_names=True)` : nouveau paramètre ; si False,
  retourne uniquement les IDs (retourne {id, name=ID, display=ID}) sans batch.
  (3) `_friends_thread` (app_ui.py) : charge les amis en IDs via
  get_friends(resolve_names=False) -> présence batch -> ne résout les pseudos
  QUE des amis EN LIGNE via `_get_users_individual` (petit set, pas 300) ->
  vrai display name + pseudo comme avant. L'ancien flux résolvait les noms de
  TOUS les amis via le batch 429 -> autant d'IDs.
  NB : le fallback resolve_player (ligne 946) utilise toujours
   get_friends(resolve_names=True) par défaut (inchangé).
- 11/09 - UPDATER IN-PLACE FIX : `apply_update()` dans updater.py ne
  tronque plus le fichier à 0 octet avant réécriture. L'ancien code
  (`truncate(0)` puis `write`) créait une fenêtre où le fichier était vide
  -> Windows Defender re-scanait et resetait l'exclusion. Nouveau code :
  `f.write(data)` puis `f.truncate()` (taille finale) — le fichier ne
  passe jamais à 0 octet, garde son inode -> exclusion Defender (path-based)
  survit. Fallback rename+copy2 supprimé. Syntaxe OK. À tester en update
  réelle par l'utilisateur.
- 30/09 - BUG FREEZE UI (app "qui met du temps à répondre") : les AVATARS
  d'amis étaient téléchargés sur le THREAD PRINCIPAL. `_render_friends()`
  (déclenché via `self.ui()` -> `_drain`) appelait `_avatars_map()` qui
  faisait `_thumb_json(timeout=10)` + `t.join(timeout=10)` DANS la boucle Tk
  -> jusqu'à ~20 s d'UI totalement gelée (d'où l'impression de "crash").
  Aggravant : rechargement auto toutes les 60 s (`friends_refresh_loop`),
  aucun cache d'avatars, destroy/recreate complet de la liste à chaque fois.
  FIX : (1) `_avatars_map` (bloquant) remplacé par `_avatar_cached()` +
  `_avatars_fetch_async(ids, on_done)` — rendu immédiat avec le cache,
  téléchargement en thread dédié, rappel via `self.ui()`, AUCUN join sur
  le principal ; (2) cache `self._avatar_cache` (user_id -> CTkImage, en
  mémoire) ; (3) timeouts avatars 4 s (`_thumb_json`/`_download_image`
  acceptent maintenant un paramètre `timeout`) ; (4) RECHARGEMENT
  MANUEL UNIQUEMENT (demande user) : suppression de `friends_refresh_loop`
  (boucle 60 s) et des `load_friends(auto=True)` dans `show_view` /
  `select_account` -> seul le bouton "⟳ Charger" déclenche (garde 3 s
  anti-double-clic conservé) ; (5) `self._friends_sig` évite le
  destroy/recreate si le contenu n'a pas changé ; (6) nouveau
  `_friends_reset()` : liste vidée au changement de compte avec le message
  "Amis de <compte> — clique sur ⟳ Charger", et le label affiche
  "N en ligne — <compte>" (plus de liste obsolète d'un autre compte).
  NB : la liste d'amis charge DÉJÀ un seul compte à la fois = celui
  sélectionné dans la vue Comptes, ou le 1er de la liste si rien n'est
  sélectionné (`_play_account`).
  PERF MESURÉE : `refresh_table_loop` (2 s) appelle `core.get_instances()`
  sur le thread principal mais ne coûte que 4-5 ms (288 processus,
  1 instance) -> NON_OPTIMISÉ VOLONTAIREMENT (risque sans gain).
  `unlock_all()` (guardian, 0,5 s) = 45 ms ≈ 9 % d'un cœur en continu :
  seul point chaud restant, inhérent au multi-instance.
  MESURE : app ouverte = 7,7 % d'UN cœur = **0,96 % du total** (8 cœurs
  logiques) — le Gestionnaire des tâches affiche ~1 %, à comparer aux
  5-15 % d'UNE instance Roblox. Donc : rien à optimiser côté CPU.
- 30/09 - FEATURE "Compte principal" + fix scroll liste des comptes :
  (1) Champ `main` par compte (unicité garantie) -> `accounts.set_main_account
  (acc_id)` (pose True sur un, False partout ailleurs), `get_main_account()`,
  et `update_account(..., main="__unset__")` (sentinelle comme
  `chrome_profile`). Au démarrage `refresh_accounts_list()` sélectionne le
  main (fallback `accs[0]` si aucun), le fait remonter EN TÊTE de liste
  (tri stable) et le préfixe de ⭐ ; `_scroll_account_into_view(idx)` le
  ramène dans le viewport après rendu. Switch « Compte principal » dans la
  fiche ⚙ (dialog 430 -> 540 px de haut).
  (2) SCROLLBAR : la scrollbar de `acc_list` est un `CTkScrollableFrame` et
  en customtkinter **6.0.0** `CTkScrollbar` ne dessine AUCUNE flèche
  (simple piste arrondie via `draw_rounded_scrollbar()`, cf.
  draw_engine.py) — d'où le "elle sert à rien". De plus `scrollbar_width`
  n'existe PAS sur `CTkScrollableFrame` en 6.0.0 (params exposés :
  `scrollbar_fg_color` / `scrollbar_button_color` /
  `scrollbar_button_hover_color`) -> impossible de "réparer" des flèches
  inexistantes. Demande clarifiée (user : "les flèches c'est pas pour
  move up des comptes ?") -> les ▲▼ RÉORDONNENT le compte SÉLECTIONNÉ
  (`accounts.move_account(acc_id, delta)`, swap + save_data, ordre
  persisté, no-op aux extrémités) ; `_sync_move_buttons` désactive ▲ si
  déjà en tête / ▼ si dernier / les 2 si <2 comptes. PAS de tri auto
  "main en tête" (rendrait le réordonnage inopérant) : le main = ⭐ +
  sélection/scroll au démarrage seulement. Scrollbars : celle du **panneau
  de droite est SUPPRIMÉE** — le panneau n'est plus un
  `CTkScrollableFrame` mais un `CTkFrame` (aucune scrollbar instanciée,
  plus rien à masquer) ; la carte "CHOISIR UN JEU" et les onglets ont été
  compactés (icône 52 px, boutons 30-34 px, onglets 205 px) pour que rien
  ne soit coupé, le panneau ne défilant plus. Liste des comptes +
  onglets AMIS/JEUX : inchangés. PIÈGE CTk 6.0.0 : `"transparent"` est
  REJETÉ sur `CTkScrollbar(button_color=)` (`ValueError: transparency is
  not allowed for this attribute`) -> ne jamais passer "transparent" là,
  utiliser `pack_forget()`. Molette + auto-scroll au démarrage restent
  actifs.
  SCROLL LISTE COMPTES : `CTkScrollableFrame` bind sa molette en
  `bind_all(..., add=True)` (bindtag `all`, donc en DERNIER) -> un binding
  additif sur `acc_list` est ignoré, CTk défile après. FIX : binding **sans
  `add`** qui renvoie `"break"` (`_acc_list_wheel`, app_ui.py:498) pour
  court-circuiter le global, + bridage `yview_moveto` entre 0 et 1 +
  `_reset_list_scroll()` quand la liste est vide.
- 30/09 - FEATURE "Fermer en arrière-plan" (icône dans la zone de
  notification) + layout 2 colonnes à la même hauteur :
  (1) `pystray` installé sur l'interpréteur Python310 (dépendance
  EXTERNE, première du projet -> à garder dans `requirements` mental :
  customtkinter + pillow + pystray, psutil déjà là via core).
  Importé sous `try/except` dans app_ui.py (`HAS_TRAY`) : si absent,
  l'app refuse de se cacher (elle deviendrait introuvable) et affiche
  « pystray manquant » dans les paramètres.
  `protocol("WM_DELETE_WINDOW", self._on_close_requested)` : si
  `settings.json["minimize_to_tray"]` -> `_hide_to_tray()` (withdraw +
  icône zone de notification) au lieu de détruire. Sinon vraie
  fermeture.
  Icône pystray dans un **thread daemon** via `icon.run_detached()`,
 Callbacks encapsulés dans `self.ui(...)` (les callbacks pystray
  tournent hors du thread Tk -> jamais de Tk direct).
  Menu : « Afficher NamaChan » (default, double-clic) / « Quitter ».
  `destroy()` appelle aussi `_tray.stop()` pour enlever l'icône.
  RÉPONSE À LA QUESTION USER : OUI, le guardian DOIT continuer en
  arrière-plan — c'est tout l'intérêt (il empêche qu'une instance qui se
  ferme proprement tue les autres). Le process vivant = guardian vivant.
  Persisté dans `apply_feature_settings` (`minimize_to_tray`).
  (2) `NamaChanAccountManager.spec` : `collect_all('pystray')` +
  `hiddenimports += ['pystray._win32']` (l'import est dans un
  try/except, PyInstaller peut ne pas le détecter).
  (3) Layout : `left` (MES COMPTES) passé de `fill="y"` à
  `fill="both", expand=True` + `tabs.pack(fill="both", expand=True)` +
  `friends_box`/`rec_grid` en `fill="both", expand=True` -> les deux
  colonnes font exactement la même hauteur et les onglets AMIS / JEUX
  occupent toute la place restante (pas de vide en bas). Inutile de
  toucher à CTkTabview : ses onglets sont déjà `grid(sticky="nsew")`
  en row 3 avec `weight=1`.

## Liste de progression (roadmap)

> **PRIORITÉ (à traiter) — Bug tooling `release.ps1`** : le script de release
> utilise `Get-Content`/`Set-Content` (lignes 37-39) pour bumper `__version__`.
> En PowerShell 5.1, ceci relit app_ui.py en ANSI (Windows-1252) et le réécrit
> en UTF-8 -> MOJIBAKE sur tous les accents (é->Ã©, etc.), et `git` voit ~300
> lignes modifiées. C'est arrivé à la v1.1.0 (08/09). À corriger pour la
> prochaine release : ne PAS éditer les sources via PowerShell ; bumper la
> version autrement (ex. remplacer uniquement `__version__` par un
> `git show` + outil d'édition, ou regex sur les octets / sed, ou demander à
> l'agent de le faire). NB : le process PyInstaller via le script est aussi
> fragile (stderr -> NativeCommandError avec $ErrorActionPreference=Stop).

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
8. [~] **QoL diverses** <- ON EN EST LÀ :
   - [x] Join by player name : entrer un pseudo Roblox -> résolution
         users API + présence API -> joint le MÊME serveur (`mode: job`,
         déjà supporté par `build_launch_uri`). UI : carte "Rejoindre un
         joueur" dans la vue Comptes.
   - [x] Refonte historique jeux récents (25/08) : grille à trous remplacée
         par une liste compacte (icône + nom + place/compte + bouton ▶).
   - [x] Diagnostic perf PC portable entry-level (i5-10300H + GTX 1650
         mobile) : modes Perf sans effet visible, CPU-bound + throttling.
         Recommandations données (FPS 60, vérifier GPU dédié Windows).
   - [x] Amis en ligne (08/09) : carte dans la vue Comptes -> bouton
         "⟳ Charger" charge les amis du compte sélectionné + présence batch
         -> liste des amis en ligne avec bouton ▶ (même serveur si gameId).
           Au passage fixé "Join by player" (bug racine : API présence renvoie
           `userPresenceType` et pas `presenceType`).
           Chargement MANUEL UNIQUEMENT (30/09 : le reload auto 60 s causait
           le freeze de l'UI, cf. plus bas — bouton ⟳ Charger seul déclenche,
           avec cache d'avatars donc quasi instantané au 2e clic).
           Ajout 08/09 : TÊTES (avatars 32px) à gauche de chaque ami en ligne
           (batch thumbnails par 100 + dl threads parallèles) ; batch users +
           présence paginés/parallélisés -> ~300 amis chargés en <1s.
   - [x] Compte principal (30/09) : switch dans la fiche ⚙ -> le compte
         ⭐ est sélectionné + scrollé automatiquement au démarrage (pas de
         tri : l'ordre reste LE tien). Boutons ▲/▼ RÉORDONNENT le compte
         sélectionné (la scrollbar CTk 6.0.0 n'a pas de flèches
         fonctionnelles).
   - [ ] Autres QoL à définir avec l'utilisateur
   - [ ] [À ÉVALUER] Backend graphique : forcer le backend de rendu
         (FFlagDebugGraphicsPreferD3D11 / PreferVulkan / PreferOpenGL —
         allowlistés) dans les modes Perf pour un léger boost selon la carte.
         À tester sur PC avant de décider (ne pas activer sans validation).
9. [x] Documentation auto : TOUS les changements (bug, feature, QoL) sont
       notés dans `JOURNAL.md` + `AGENTS.md` à chaque modification.
10. [ ] **Ne jamais push GitHub sans demander** : laisser l'utilisateur
        tester l'exe sur PC avant de créer la release. Réduire le nombre
        de releases (pas 1 fix = 1 release).
11. [x] Updater in-place fix : `apply_update()` écrit par-dessus sans
        tronquer à 0 → Windows Defender ne reset plus l'exclusion à chaque
        update (11/09, à tester en conditions réelles).

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
- RÈGLE : à CHAQUE modification (bug, feature, QoL, ajustement) ->
  documenter dans `AGENTS.md` (notes techniques, section État/historique)
  ET `JOURNAL.md` (récit lisible : contexte -> cause -> fix).
  Systématique, pas seulement les bugs "majeurs".
- RÈGLE : documenter AUTOMATIQUEMENT chaque modification, même si
  l'utilisateur ne le demande pas explicitement (il quitte parfois sans
  y penser). Ajouter un todo dans la roadmap si la feature a des étapes
  restantes.
- Après chaque modif : tester en lançant `python app_ui.py`.
  NB : le bon interpréteur est
  `C:\Users\namaz\AppData\Local\Programs\Python\Python310\python.exe`
  (Python 3.14 système n'a PAS customtkinter ; `python` seul pointe vers le
  stub Microsoft Store).
- Rebuild exe : PyInstaller avec `NamaChanAccountManager.spec`, puis TOUJOURS
  copier `dist\NamaChanAccountManager.exe` sur le Bureau (l'utilisateur y
  lance l'exe) :
  `Copy-Item dist\NamaChanAccountManager.exe ([Environment]::GetFolderPath('Desktop')) -Force`
- Release GitHub : TOUJOURS inclure l'exe dans la release
  (`gh release upload ... --clobber`). NE JAMAIS push sur GitHub sans demander
  d'abord : laisser l'utilisateur tester sur PC avant.
- Release v1.1.1 (30/09) : l'exe du **Bureau n'a PAS été écrasé** (il date
  du 11/09) — l'utilisateur update lui-même via l'updater pour tester.
  L'exe à jour est dans `dist\` + la release GitHub.
- CONTENU DE LA PAGE GITHUB : uniquement le **résumé de ce qu'apporte
  l'update** (destiné aux users). Ne PAS y mettre les checklists de test,
  les rappels internes (« l'exe du Bureau n'a pas été écrasé »), ni le
  contexte de session (PC de l'ami, comptes perso, etc.) : tout ça reste
  dans AGENTS.md / JOURNAL.md.
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

## LLM locaux (setup 22/09) — hors code NamaChan

- Pour opencode, l'utilisateur a installé 2 modèles Ollama LOCAUX sur le disque
  D: (2ᵉ SSD) comme alternative gratuite/privée au cloud (moi, big-pickle).
- Emplacement : `D:\ollama\models` via la var utilisateur `OLLAMA_MODELS`
  (persistée dans le registre). Le dossier racine D: étant non-écrivable par le
  compte (ACL admin), le dossier a dû être créé en admin + ACL héritée
  `(OI)(CI)F` ajoutée (sinon sous-dossier `blobs` = readonly -> pull fails
  "Access is denied").
- Modèles : `qwen2.5-coder:14b` (9 Go) et `LoPld/qwen3-coder-30b-a3b-q4_K_S`
  (17 Go, MoE 30B-A3B = ~3,3B actifs → vitesse de 14B avec qualité proche 30B).
- `qwen3-coder` n'a PAS de tag 14B (série Qwen3 pas de petit dense) ; le 14B
  = `qwen2.5-coder:14b`. RTX 5080 16 Go (~14,6 dispo) : le 30B en Q4_K_S passe
  de justesse avec un léger offload mémoire.
- Config : `opencode.json` -> provider `ollama` avec les 2 modèles enregistrés
  (`tool_call: true`). Le modèle par défaut reste le cloud (big-pickle).
  NB : config opencode chargée au démarrage uniquement -> redémarrage requis
  après toute modif de `opencode.json`.
- IMPORTANT : le provider `ollama` N'EST PAS dans le catalogue opencode/models.dev
  (seul `ollama-cloud` y est). Sans `npm` ni `options.baseURL`, opencode envoie
  la requête sur `undefined/chat/completions` -> "cannot be parsed as a URL".
  FIX appliqué le 22/09 : ajout de `"npm": "@ai-sdk/openai-compatible"` +
  `"options": { "baseURL": "http://localhost:11434/v1" }`. Endpoint vérifié :
  `GET http://localhost:11434/v1/models` renvoie bien les 2 modèles.
- Pas d'impact sur le code NamaChan : c'est du pur outillage opencode.
