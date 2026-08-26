# Journal des bugs & correctifs — NamaChan Account Manager

Historique lisible des problèmes rencontrés et de comment ils ont été
résolus. Les notes techniques courtes pour les agents vivent dans `AGENTS.md`.

---

## 23/08 au matin — Le multi-instance était totalement mort

**Symptôme** : lancer plusieurs Roblox ne marchait pas, le tableau des
instances restait vide.

**Causes trouvées (4 bugs cumulés !)** :
1. `global _proc_cache` manquant dans `core.get_instances()` → le cache
   interne ne se remplissait jamais, tableau mort.
2. Les codes retour NTSTATUS étaient stockés en non-signé (`c_ulong`) alors
   que Windows renvoie des valeurs signées → la comparaison avec
   `STATUS_INFO_LENGTH_MISMATCH` (0xC0000004) était TOUJOURS fausse →
   l'énumération des handles système n'était jamais tentée.
3. Windows 11 24H2+ a changé le layout mémoire de
   `SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX` : entrées de 40 octets avec champs
   réordonnés `[PID u64][Handle u64][Access u32][CBTI u16][TypeIdx u16][Object u64][Res u32]`
   + en-tête de 24 octets avant le tableau.
4. Roblox n'utilise plus `ROBLOX_SINGLEINSTANCE` mais de nouveaux objets :
   `ROBLOX_singletonMutex`, `ROBLOX_singletonEvent`, `<chemin exe>.mtx`
   et `<chemin exe>.shm` (IPC "warm start"). Il faut stripper Mutant + Event
   + Section avec un matching élargi.

**Résultat** : multi OK, 3+ instances parallèles validées.

---

## 23/08 après-midi — Ne JAMAIS stripper un Roblox jeune

**Symptôme** : en strippant en continu pendant le boot, les instances se
fermaient en boucle et semblaient se « réinstaller » sans fin.

**Cause** : le processus bootstrap/updateur de Roblox s'appelle LUI AUSSI
`RobloxPlayerBeta.exe`. Stripping ses objets pendant qu'il crée son
environnement = on casse son démarrage → kill/respawn en boucle.

**Règle d'or** : `unlock_all(min_age=8.0)` — on ne touche QUE les processus
de plus de 8 secondes. Jamais les jeunes.

---

## 23/08 soir — LE gros bug : fermer une fenêtre fermait TOUTES les autres

### Symptôme
Avec 2+ instances ouvertes, cliquer sur le X d'UNE fenêtre → toutes les
autres se fermaient toutes seules ~4 secondes après (fermeture propre,
pas un crash). Et il restait des processus fantômes (~100 MB) en
arrière-plan sans fenêtre.

### Déclencheur
Une mise à jour Roblox déployée ce soir-là (versions `ddf602d9` et
`2f3eb5f`, modifiées à 18h51 et 18h54). Avant cette update, le multi
marchait parfaitement.

### Enquête (scripts de test jetables, tous dans le projet)
1. **`test_close_prop.py`** — reproduit le scénario réel : lance 2 comptes
   via ticket CDP comme l'app, puis envoie `WM_CLOSE` (= clic sur X) à la
   première. → **REPRODUIT** : la seconde meurt 4,1 s après, teardown
   propre (« SessionTransitionFSM Tearing down » dans ses logs).
2. **`test_hardkill.py`** — même chose mais on TUe brutalement la première
   (`TerminateProcess`). → **la seconde SURVIT**.
   Conclusion : ce n'est PAS la mort de l'instance en soi, c'est quelque
   chose qu'elle fait pendant sa fermeture PROPRE qui signale les autres.
3. Constat clé : les instances recréent leurs objets kernel nommés en
   continu. Un strip ponctuel avant lancement ne suffit pas : dès que
   Roblox recrée l'objet, le canal de signalisation est de nouveau ouvert.
4. **`test_guardian.py`** — teste la parade : un thread re-strippe en boucle
   (toutes les 400 ms) les objets single-instance de la 2e instance
   pendant qu'on ferme la 1re proprement. → **la 2e instance SURVIT**.

### Cause
Dans sa nouvelle version, quand une instance Roblox ferme PROPREMENT, elle
signale sa fermeture via un objet kernel nommé (Event/Mutant de la famille
singleton). Les autres instances écoutent ce signal et se ferment aussi
(teardown volontaire, pas un crash).

### Fix
**Le gardien** dans `core.py` :
- `start_guardian()` / `stop_guardian()` : thread daemon qui, tant que le
  switch Multi est ON, re-strippe les objets single-instance de TOUTES les
  instances de plus de 8 s, toutes les 0,5 s.
- Respecte la règle des 8 s (jamais sur les jeunes en boot).
- Branché dans `app_ui.py` : `_sync_guardian()` appelé par le switch
  Multi (via `apply_feature_settings`), message dans la Console à ON/OFF,
  arrêt propre à la fermeture de l'app.

### Validation
`test_guardian.py` : B survit à la fermeture propre de A ✓
Re-testé ensuite par l'utilisateur en conditions réelles ✓

---

## Les fantômes en arrière-plan (comportement Roblox, pas un bug)

Après fermeture, Roblox laisse parfois des processus `RobloxPlayerBeta.exe`
sans fenêtre (~100 MB). L'app les détecte (`pid_has_visible_window` via
EnumWindows) et affiche leur statut « Arrière-plan » dans le tableau au
lieu de « En jeu/app ».

NB : relancer un compte déjà connecté ailleurs déconnecte l'ancienne
session — comportement normal de Roblox, pas lié au multi.

---

## Le cap FPS perdu après une mise à jour Roblox (24/08)

### Symptôme
Après une MAJ automatique de Roblox, les FPS retombent à 30/60 alors que
l'utilisateur avait réglé 240. Le multi-instance continue de marcher, mais
la limite FPS a disparu.

### Cause
Le cap FPS est écrit dans
`%LOCALAPPDATA%\Roblox\Versions\version-XXXX\ClientSettings\ClientAppSettings.json`.
Une MAJ Roblox crée un NOUVEAU dossier `version-XXXX` (et supprime l'ancien)
→ le fichier avec le cap disparaît avec l'ancienne version.
Le réglage était bien sauvegardé dans `settings.json` (`fps_default`),
mais il n'était JAMAIS ré-appliqué automatiquement : il fallait re-cliquer
sur « Appliquer FastFlags » à la main après chaque MAJ.

### Fix
- `core.py` : nouvelle fonction `ensure_fps_cap()` — lit `fps_default` des
  settings, compare avec le `DFIntTaskSchedulerTargetFps` présent dans chaque
  `version-*`, et ne réécrit que si une version manque ou diffère (idempotent,
  pas d'écriture inutile).
- `app_ui.py` : appelé au démarrage de l'app (log dans la Console si
  réapplication) ET avant chaque lancement de compte (`api_launch`, juste
  avant le Popen) — donc même si l'app tournait pendant la MAJ, le prochain
  lancement repart avec le bon cap.

---

## Qualité graphique qui repasse en « Automatique » en multi (25/08)

### Symptôme
En multi-instance, le mode graphique de Roblox (menu Échap → Settings)
repasse sur **Automatique** au lieu de rester **Manuel**, sans que personne
n'y touche.

### Enquête
- Vérifié les `ClientAppSettings.json` de toutes les versions : NamaChan
  n'écrit QUE les 2 flags FPS (`DFIntTaskSchedulerTargetFps`,
  `FFlagTaskSchedulerLimitTargetFpsTo2402`). Aucun flag qualité n'est posé
  par l'app.
- Le mode Auto/Manuel n'est PAS un FastFlag : il est persisté par le client
  dans `%LOCALAPPDATA%\Roblox\rbx-storage` (base type LevelDB), partagée par
  TOUTES les instances tournant sous le même profil Windows.
- Conclusion : quand plusieurs instances tournent et se ferment, elles se
  réécrivent cette base concurrentlement → le dernier écrivain gagne, et le
  mode peut revenir à Automatique. Effet de bord du multi Roblox lui-même,
  pas un bug d'écriture de NamaChan.

### Fix
Forcer la qualité côté FastFlags, pour que le mode Auto/Manuel du menu
devienne sans conséquence :
- `core.py` : `apply_fps_cap(fps, gfx_mode)` écrit aussi
  `DFIntDebugFRMQualityLevelOverride` (niveau FRM 1–21) selon le mode ;
  en mode `auto` le flag est RETIRÉ (pas de forçage). `ensure_fps_cap()`
  vérifie désormais FPS + flag qualité (réapplique après MAJ Roblox aussi).
  Constantes : `GFX_FLAG`, `GFX_QUALITY_LEVELS` = perf→1, equilibre→8,
  pro→21 ; `GFX_LABELS` pour l'UI.
- `app_ui.py` : vue Multi → menu « Qualité : » à côté du cap FPS
  (Auto / Perf / Équilibré / Pro). Le bouton « Appliquer FastFlags » applique
  les deux et sauvegarde `settings.json["gfx_quality"]`.
  NB : clé ajoutée dans `apply_feature_settings()` sinon effacée à chaque
  application des settings (piège déjà connu).
- Testé : écriture/retrait du flag sur les 5 versions, `ensure_fps_cap`
  recrée un fichier supprimé avec le bon état, UI lancée OK.

---

## FPS plafonnés à 120 malgré le FastFlag à 240 (25/08)

### Symptôme
L'utilisateur a mis 240 dans « Limite FPS » (et le FastFlag
`DFIntTaskSchedulerTargetFps=240` était bien écrit), mais en jeu le compteur
reste bloqué à ~120.

### Cause
Roblox a ajouté un réglage officiel **« Maximum Frame Rate »** dans le menu
Échap → Settings. Il est stocké dans un AUTRE fichier que les FastFlags :
`%LOCALAPPDATA%\Roblox\GlobalBasicSettings_13.xml`
(`<int name="FramerateCap">120</int>`). Depuis la refonte du système de caps
(allowlist FastFlags, fin 2025), CE réglage écrase la valeur du FastFlag.
Le tien était resté à 120 → cap effectif 120, peu importe le flag.

### Fix
- `core.py` : nouvelles fonctions `write_global_framerate_cap(fps)` /
  `read_global_framerate_cap()` qui lisent/écrivent la balise FramerateCap du
  XML (regex ciblée, le reste du fichier est intact). `apply_fps_cap()`
  appelle l'écriture à chaque application ; `ensure_fps_cap()` compare aussi
  cette valeur au démarrage et avant chaque lancement -> si une instance ou
  le menu Échap remet 120, c'est réaligné automatiquement.
- NB : comme rbx-storage, ce XML est partagé par toutes les instances ->
  changer le cap ne s'applique qu'aux instances lancées APRÈS (celles déjà
  ouvertes réécrivent leur valeur en se fermant).

---

## Mode Perf enrichi (25/08)

Demande utilisateur : sky désactivé + render distance max + textures min.

`GFX_PRESET_FLAGS["perf"]` (core.py) ajoute aux flags de base :
- `FFlagDebugSkyGray=True` : ciel remplacé par gris plat (moins de GPU).
- `DFFlagTextureQualityOverrideEnabled=True` + `DFIntTextureQualityOverride=0`
  : textures forcées au minimum quel que soit le niveau FRM.
- Distances de switch LOD CSG (`DFIntCSGLevelOfDetailSwitchingDistance*`)
  poussées à 100000 : les meshes restent détaillés très loin (render distance
  max). Coût GPU plus élevé de loin, mais demandé explicitement.
Les modes Auto/Équilibré/Pro n'ont AUCUN de ces extras : passer d'un mode à
l'autre retire proprement tous les flags gérés (`GFX_MANAGED_KEYS`).

### Retours utilisateur (25/08, suite)
- 240 FPS : OK (le fix FramerateCap a fonctionné).
- Ciel toujours pas gris -> `FFlagDebugSkyGray` probablement ignoré par les
  clients récents (hors allowlist). Gardé dans les presets, mais si ça ne
  marche pas il n'y a pas d'alternative par config (il faudrait un mod
  d'assets type bootstrapper).
- Blox Fruits : « je vois le vide » en Perf -> cause comprise : le niveau FRM
  1 réduit la zone rendue elle-même ; les distances LOD ne compensent pas.
  -> Nouveau mode **Perf++** (test) : niveau FRM 10 (grande distance rendue)
  + textures quand même forcées mini via override + ombres/lumière voxel
  coupées pour compenser le coût + LOD doublés (200000). À comparer avec Perf.

---

## « Vision pas au max » en Perf++ alors que les settings manuels au max marchent (25/08)

### Symptôme
Avec Perf++ (moteur forcé niveau 21), l'utilisateur voit toujours moins loin
sur Blox Fruits que lorsqu'il met lui-même les graphismes Roblox au maximum
dans le menu Échap. Pourtant FRM 21 = slider max...

### Cause
Le rayon de **streaming** (ce que le serveur accepte d'envoyer au client
autour du joueur) ne suit PAS le FastFlag `DFIntDebugFRMQualityLevelOverride`
: il suit le réglage utilisateur RÉEL stocké dans
`%LOCALAPPDATA%\Roblox\GlobalBasicSettings_13.xml` :
- `<int name="GraphicsQualityLevel">` (niveau courant)
- `<token name="SavedQualityLevel">` (préférence : 0 = Auto, 1-10 = Manuel)
L'utilisateur avait `SavedQualityLevel=0` (Auto) -> le client demandait un
rayon de streaming modeste -> îles lointaines jamais reçues -> vide, même
avec un moteur réglé pour tout dessiner.

### Fix
- `core.py` : `write_global_quality_level(10)` / `read_global_quality_level()`
  (regex sur balises `<int>` OU `<token>` — les deux clés n'ont pas le même
  type !). Appliqué par `apply_fps_cap()` quand gfx_mode est perfplus ou pro ;
  vérifié par `ensure_fps_cap()`. Résultat : Perf++ = vrais réglages à fond
  (streaming inclus) + textures mini + ombres coupées.
- Pièges au passage : `SavedQualityLevel` est un `<token>`,
  `GraphicsQualityLevel` un `<int>` ; `FIntCameraFarZPlane` (plan de coupe
  caméra) a été testé puis retiré — hors allowlist donc ignoré silencieusement
  (aucun risque de ban, juste inutile).

---

## Bilan des modes qualité + inversion Perf / Perf++ (25/08 au soir)

### Ce qui a été réglé avant l'inversion
- L'utilisateur confirme « je vois toute la map » -> le fix
  `SavedQualityLevel=10` marche. Décision : l'écriture est étendue à TOUS les
  modes forcés (tout sauf Auto), pour que même Perf garde la vision complète.
- Les ombres coupées (`FIntRenderShadowIntensity=0` +
  `DFFlagDebugPauseVoxelizer=True`) sont ajoutées AUSSI au mode Perf ->
  Perf et Perf++ partagent désormais exactement les mêmes optimisations ;
  seule différence restante : le niveau moteur FRM 1 vs 21 (= détail
  géométrique des objets au loin, seul coût GPU supplémentaire possible).

### L'inversion demandée par l'utilisateur
L'utilisateur veut que **Perf++ soit LE mode FPS max** (« ++ » = encore plus
de performances). Or à ce moment Perf++ désignait le mode détaillé (FRM 21).
Fix : simple permutation des LIBELLÉS, pas des presets :
- `GFX_LABELS` (core.py) : « Perf++ » -> preset `perf` (FRM 1, FPS max
  absolu), « Perf » -> preset `perfplus` (FRM 21, vision détaillée).
- Tooltips (`GFX_TOOLTIPS`, app_ui.py) mis en cohérence.
- Ordre du menu changé pour refléter la logique « du plus léger au plus
  beau » : **Auto / Perf++ / Perf / Équilibré / Pro**.
Les presets internes (`perf`, `perfplus`) gardent leur contenu : les
settings déjà sauvegardés restent valides, seul l'affichage change.

### État final des modes
| Mode | Moteur | Textures | Ombres | Vision | Usage |
|------|--------|----------|--------|--------|-------|
| Auto | — | — | — | réglage Roblox | laisser faire |
| Perf++ | FRM 1 | mini | off | complète, rendu minimal | FPS max absolu |
| Perf | FRM 21 | mini | off | complète ET détaillée | joli + fluide |
| Équilibré | FRM 8 | normales | normales | complète | compromis |
| Pro | FRM 21 | normales | normales | complète | max visuel |

NB persistant : le ciel gris (`FFlagDebugSkyGray`) reste sans effet visible
chez l'utilisateur (probablement hors allowlist des clients récents).

---

## Revue de code AntiAFK / AutoRejoin — intégration confirmée (25/08)

Revue complète du câblage : les deux features étaient déjà intégrées de bout
en bout, la roadmap passe en [x].
- **AntiAFK** : switch + intervalle persistés (`settings.json["anti_afk"]`
  / `["aa_interval"]`, min 15 s), thread démarré au boot si activé ; envoie
  VK_F13 via PostMessageW (WM_KEYDOWN/KEYUP) à toutes les fenêtres VISIBLES
  des instances. Technique standard, pas d'injection dans le processus.
- **AutoRejoin** : chaque `api_launch` tracke `(pid, compte, target)` ;
  boucle 3 s détecte les PIDs morts, relance après délai avec la MÊME target
  (mode job = même serveur), le nouveau PID est re-tracké automatiquement.
- Limite connue documentée DANS l'UI (vue Features) : impossible de
  distinguer fermeture volontaire vs déconnexion -> switch ON = toute
  instance fermée est relancée, y compris à la main. Note affichée sous le
  switch pour prévenir l'utilisateur.
- Reste à tester en conditions réelles par l'utilisateur : idle 20-30 min
  (AntiAFK), fermeture manuelle avec switch ON (AutoRejoin).




