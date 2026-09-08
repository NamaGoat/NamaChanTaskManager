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
| Mode | Moteur | Textures | Ombres | Ciel | LOD | RestrictGC | Vision |
|------|--------|----------|--------|------|-----|------------|--------|
| Auto | — | — | — | Roblox | — | — | réglage Roblox |
| Perf++ | FRM 1 | mini | off | gris | 200k | 500k | complète, rendu minimal |
| Perf | FRM 21 | mini | off | normal | 200k | 500k | complète ET détaillée |
| Équilibré | FRM 8 | normales | normales | Roblox | — | — | complète |
| Pro | FRM 21 | normales | normales | Roblox | — | — | max visuel |

NB : la nuit noire (sans FFlagDebugSkyGray) est un comportement normal
Roblox — pas un bug.

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

---

## Updater : "Security validation failure" (v1.0.3 → v1.0.10, 26/08)

### Symptôme
L'update depuis l'exe (auto-update intégrée) échouait avec une erreur
"Security validation failure" de PyInstaller lors du remplacement du fichier.

### Cause
PyInstaller embarque un mécanisme de sécurité : quand un exe lancé depuis
un chemin A tente de remplacer son propre fichier, il doit passer par le
**même répertoire**. L'updater téléchargeait d'abord vers un dossier
temporaire (`tempfile`) puis tentait de déplacer le fichier dans le dossier
de l'exe → PyInstaller rejetait l'opération car le chemin source n'était
pas le répertoire de l'app.

### Fixes successifs (5 itérations, v1.0.3 à v1.0.10)
1. **v1.0.3** — retry loop pour les file locks + délais plus longs après
   `taskkill` (premier fix, pas encore le bon).
2. **v1.0.4** — l'updater télécharge désormais dans le **même répertoire**
   que l'exe (plus de dossier temp) + fix du path de sécurité PyInstaller.
3. **v1.0.5–1.0.6** — tentative de nettoyage via dossier temp + scripts
   batch → même erreur de sécurité.
4. **v1.0.7–1.0.9** — passage à VBScript puis PowerShell pour contourner
   la validation de sécurité du batch PyInstaller ; fix du problème où
   Python s'arrêtait AVANT que le script PS1 ait pu swap les fichiers.
5. **v1.0.10** — revert du parser de version (4 parties → 3 parties) qui
   cassait la comparaison de versions.

### Fix final (état actuel)
- L'updater télécharge dans le **même répertoire** que l'exe (pas de
  temporaire), ce qui satisfait PyInstaller.
- `cleanup_orphan_files()` au démarrage supprime les fichiers `.new`/`.old`
  restants d'un update précédent (si le swap a planté entre-temps).
- Le script PowerShell (`.ps1`) gère le remplacement : kill process → wait →
  rename old → rename new → relance.

### Validation
Release v1.0.10 uploadée sur GitHub avec exe fonctionnel. L'update depuis
l'exe a été testée avec succès par l'utilisateur ✓

---

## Modes Perf : render max + suppression ciel gris Perf (26/08)

### Problème
- Perf++ (FRM 1) : la map n'était pas rendue au max → LOD à 100000 et pas
  de `DFIntDebugRestrictGCDistance` → zone rendue trop petite malgré le
  streaming qui envoie les données.
- Perf (FRM 21) : le ciel gris (`FFlagDebugSkyGray`) causait un fog gris
  indésirable. L'utilisateur voulait un ciel naturel sur ce mode.
- Les deux modes avaient des settings qualité identiques sauf le FRM → les
  optimisations n'étaient pas alignées.

### Fix
- Les deux modes Perf poussent désormais le render distance au max :
  LOD à 200000 (tous les `_LOD_KEYS`) + `DFIntDebugRestrictGCDistance=500000`
  (lève la restriction de distance de dessin).
- `FFlagDebugSkyGray` retiré UNIQUEMENT du preset `perfplus` (UI "Perf") :
  ciel normal, nuit visible. Reste sur `perf` (UI "Perf++") pour ceux qui
  veulent le gris.
- Qualité minimale conservée sur les deux modes : textures mini
  (`DFIntTextureQualityOverride=0`), ombres off
  (`FIntRenderShadowIntensity=0` + `DFFlagDebugPauseVoxelizer=True`).
- Tooltips (`GFX_TOOLTIPS`, app_ui.py) mis à jour.

### Note
La nuit noire sur Blox Fruits est un comportement normal de Roblox
(quand FFlagDebugSkyGray n'est pas actif), pas un bug à corriger.

---

## Allowlist FastFlags Roblox + textures (26/08, v1.0.11–v1.0.15)

### Contexte
Depuis le **29 septembre 2025**, Roblox a introduit le **Fast Flag
Allowlist** : seuls les flags sur une liste blanche officielle sont
reconnus par le client. Tout le reste est ignoré silencieusement.

### Flags allowlistés (rendering)
```
DFIntDebugFRMQualityLevelOverride    ← FRM (1–21)
DFIntTextureQualityOverride          ← qualité textures (1=mini, auto=0)
DFFlagTextureQualityOverrideEnabled  ← active l'override textures
DFFlagDisableDPIScale                ← désactive DPI scaling
DFFlagDebugPauseVoxelizer            ← coupe le voxelizer
FFlagDebugSkyGray                    ← ciel gris
FIntDebugForceMSAASamples            ← MSAA
DFIntCSGLevelOfDetailSwitchingDistance* ← LOD (4 distances)
FFlagDebugGraphicsPreferD3D11/Vulkan/OpenGL
FFlagHandleAltEnterFullscreenManually
```

### Flags NON allowlistés (ignorés par Roblox)
- `DFIntDebugRestrictGCDistance` — censé lever la restriction de distance
  de dessin, mais ignoré.
- `FIntRenderShadowIntensity` — censé couper les ombres, ignoré.
- `FIntDebugTextureManagerSkipMips`, `DFIntPerformanceControlTextureQualityBestUtility`
  — testés dans v1.0.14, virés car ignorés.

### Fix textures : `DFIntTextureQualityOverride` 0 → 1
`0` = **Auto** (Roblox décide selon le hardware), pas "minimum".
Passé à `1` pour forcer le minimum. Ce flag est dans l'allowlist et
marche, mais Roblox a migré vers **TextureManager2** qui interprète
le flag différemment — les textures sont pas aussi basses qu'avant.

### Nouveau mode : Perf Render Max (v1.0.15)
- FRM 21 (moteur max) + textures mini + ombres off + render max.
- Pour les PC qui veulent voir au loin tout en gardant les optimisations.
- Ordre menu : Auto / Perf++ / Perf / Perf Render Max / Équilibré / Pro.

### Différence AMD vs NVIDIA
Sur les tests effectués :
- NVIDIA (RTX 5080, GTX 1650) : FRM 1 → vision complète ✓
- AMD (RX 6750 XT) : FRM 1 → vision réduite, void au loin ✗

Le moteur Roblox interprète le FRM différemment selon le constructeur
de la carte graphique. Les cartes AMD semblent couper le streaming plus
agressivement avec un FRM bas. Solution : utiliser Perf Render Max (FRM 21)
ou Pro sur les cartes AMD.

### Bilan modes qualité (mis à jour)
| Mode | FRM | Ciel | Textures | Ombres | LOD | Usage |
|------|-----|------|----------|--------|-----|-------|
| Auto | — | Roblox | — | — | — | laisser faire |
| Perf++ | 1 | gris | mini | off | 200k | FPS max (NVIDIA) |
| Perf | 1 | normal | mini | off | 200k | FPS max sans fog |
| Perf Render Max | 21 | normal | mini | off | 200k | voit au loin + perf |
| Équilibré | 8 | — | normales | normales | — | compromis |
| Pro | 21 | — | normales | normales | — | max visuel |

NB : les flags `FIntRenderShadowIntensity` et `DFIntDebugRestrictGCDistance`
ne sont pas dans l'allowlist et sont possiblement ignorés. Les ombres
et la restriction de distance dépendent donc du moteur Roblox lui-même.



---

## Nettoyage + boost modes Perf (26/08)

### Problème
Les modes Perf (perf/perfplus/perfrendermax) contenaient 2 flags qui
ne font plus AUCUN effet depuis l'allowlist Roblox (29/09/2025) :
- `FIntRenderShadowIntensity` (ombres off) — hors allowlist, ignoré.
- `DFIntDebugRestrictGCDistance` (render distance max) — hors allowlist, ignoré.

Ils donnaient une fausse impression d'optimisation et brouillaient le code.

### Fix
- Les 2 flags morts retirés des 3 presets Perf.
- 3 flags allowlistés AJOUTÉS (donc réellement reconnus par le client) :
  `FIntDebugForceMSAASamples=-1` (anti-aliasing off),
  `FIntFRMMinGrassDistance=0` + `FIntFRMMaxGrassDistance=0` (herbe off).
- Le seul levier "ombres douces" restant est `DFFlagDebugPauseVoxelizer`
  (déjà présent). Les ombres dures ne sont pas coupables via allowlist.
- `FFlagDebugSkyGray` conservé seulement sur le preset `perf` (UI Perf++).

### Validation
Import `core` OK. À tester par l'utilisateur sur PC (modes Perf++/Perf/
Perf Render Max) avant toute release GitHub.

---

## UI : détail modes qualité + confirmation double-clic (26/08)

### Contexte
L'utilisateur ne voyait pas clairement tout ce que fait chaque mode de
qualité (Perf++, Perf, Perf Render Max...). Et les popups (messagebox) pour
confirmer les actions (Kill TOUT) étaient pénibles à fermer.

### Fix
1. **Tooltips qualité enrichis** (GFX_TOOLTIPS, app_ui.py) : chaque mode
   liste ligne par ligne ce qu'il applique (niveau moteur FRM, textures mini,
   anti-aliasing off, herbe off, ombres douces off, ciel gris/normal, LOD max)
   + un avertissement AMD pour Perf++.
   Ajout d'un bouton **"ℹ"** à côté du menu Qualité (vue Multi) qui affiche
   le même détail au survol — aucune nouvelle fenêtre (tooltip auto).
2. **Confirmation en double-clic** pour Kill sélection et Kill TOUT :
   premier clic -> le bouton change de texte en "Confirmer ?", second clic
   dans 2,5 s -> exécute. Plus de popup messagebox. Réalisé via
   `self._arm` + `_armed_kill()` / `_disarm()`.

### Validation
Syntaxe OK, import OK, exe rebuildé et copié sur le Bureau (après fermeture
de l'exe en cours). À tester par l'utilisateur.

---

## Bug Updater : remplacement échoué (restait en v16) (26/08)

### Contexte
L'utilisateur (ami) avec la v1.0.16 voyait bien l'update v1.0.17 se détecter
et se télécharger, mais après redémarrage l'app restait en v16 : le fichier
téléchargé ne remplaçait pas l'exe.

### Cause
updater.download_update() téléchargeait dans 	empfile.gettempdir()
(dossier TEMP système), et pply_update() faisait un Copy-Item du fichier
temp vers l'exe (Bureau). La validation PyInstaller/antivirus rejetait le
remplacement d'un exe depuis un dossier temporaire -> le .new était
téléchargé mais jamais appliqué. C'était le fix documenté en v1.0.10 (download
dans le même répertoire que l'exe) qui avait été régressé/perdu dans le code.

### Fix
download_update() télécharge désormais 
amachan_update.new dans le MÊME
répertoire que l'exe (os.path.dirname(sys.argv[0]), ex. le Bureau) puis
pply_update le copie sur place. cleanup_old_files() nettoie déjà les

amachan_update.* au démarrage.

### Remarque
Le bug étant dans l'updater lui-même, il faut livrer un NOUVEAU build (v1.0.18)
pour que le fix soit effectif. Un simple nouvel "update" via l'ancien updater
n'aurait pas corrigé le remplacement.

---

## Bug Updater : script PowerShell tué à la mort du parent (v1.0.19, 26/08)

### Contexte
Test d'auto-update v1.0.18 -> v1.0.19 : la MAJ est détectée, le download se
fait, mais après redémarrage l'app reste en 0.18. Sur le Bureau il ne restait
ni .old ni .new après l'échec.

### Cause
Le téléchargement (fait dans le process Python, avant os._exit(0)) réussit
et crée 
amachan_update.new. Mais le script PowerShell de remplacement était
lancé en DETACHED_PROCESS **sans** CREATE_BREAKAWAY_FROM_JOB. Or PyInstaller
en mode onefile enferme l'app dans un **Job Object** qui tue tous les
sous-processus quand le parent meurt. Quand l'app faisait os._exit(0), le
PowerShell était tué AVANT le rename/copy. Du coup le .new restait seul,
puis était supprimé au démarrage suivant par cleanup_old_files() -> "il ne
reste rien" et toujours l'ancienne version.

### Fix
Ajout de CREATE_BREAKAWAY_FROM_JOB ( x01000000) aux creationflags du
Popen : le PowerShell s'affranchit du job object PyInstaller et survit à la
mort du parent, donc le rename/copy/relance s'exécute.

### Validation
Syntaxe OK. Exe v1.0.20 rebuildé et copié sur le Bureau. À retester en
auto-update (v1.0.19 -> v1.0.20) par l'utilisateur.

---

## Fix DEFINITIF Updater : remplacement en pur Python (v1.0.22, 26/08)

### Contexte
Le BREAKAWAY_FROM_JOB (v1.0.20) n'a pas suffi : le .new restait présent
sur le Bureau et l'app restait sur l'ancienne version après relance.

### Cause
Le script PowerShell détaché restait fragile :
- Écrit en UTF-8 sans BOM, lu en ANSI par PowerShell 5.1 -> chemin corrompu
  si le dossier contient des caractères non-ASCII (nom d'utilisateur avec
  accent, etc.) -> les opérations échouaient silencieusement.
- Selon l'environnement, le process détaché pouvait aussi être tué à la mort
  du parent (job object PyInstaller onefile) malgré le BREAKAWAY.

### Fix
pply_update() ne passe PLUS par PowerShell. Le remplacement se fait en
PUR PYTHON, de façon synchrone dans le process, avant os._exit(0) :
1. os.rename(exe -> .old) : renommer un exe en cours d'execution est
   autorisé sur Windows (on le renomme, on n'ecrit pas dedans). Retry si
   verrouille.
2. shutil.copy2(.new -> exe) : le chemin etait libre apres le rename.
3. Relance du nouveau exe en DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
   | 0x01000000, puis os._exit(0).

Plus aucun sous-processus PowerShell a maintenir en vie, plus de probleme
d'encodage du script. import tempfile supprime, import shutil ajoute.

### Validation
Syntaxe + import OK. Exe v1.0.22 rebuild. A retester en auto-update
(v1.0.21 -> v1.0.22) par l'utilisateur.

---

## Popup PyInstaller à l'update : suppression de la relance auto (v1.0.24, 26/08)

### Contexte
L'update installe bien la nouvelle version, mais PyInstaller affiche au
lancement du nouvel exe : "Security validation failure - failed to obtain
exe path for parent process".

### Cause
Ce popup est lie a la RELANCE AUTOMATIQUE du nouvel exe juste apres le
remplacement : le nouveau process est lance alors que le parent vient de
mourir (contexte de validation PyInstaller inhabituel) -> warning.

### Fix
pply_update() ne relance PLUS le nouveau exe automatiquement. Il fait le
remplacement (rename -> .old, copy2 -> exe) puis os._exit(0). L'UI affiche
"mise a jour installee ! Relance l'app" et l'utilisateur relance manuellement
-> pas de process enfant relance automatiquement, plus de popup.

### Validation
Syntaxe OK. Exe v1.0.24 rebuild. A retester en auto-update par l'utilisateur.

---

## Diagnostic perf : PC portable entry-level (26/08)

### Contexte
L'ami de l'utilisateur testait les modes Perf sur un PC portable :
**i5-10300H** (4C/8T, Comet Lake 2020, laptop) + **GTX 1650 mobile 4GB**.
Aucun gain visible des modes Perf++/Perf par rapport à Auto.

### Cause
- **CPU-bound** : Roblox est avant tout limité par le CPU. Sur laptop,
  le TDP est plafonné et le CPU throttle thermiquement (90-95°C sous charge).
  Les modes Perf ne réduisent que la charge GPU (textures, ombres, AA) —
  or le GPU n'est déjà pas le bottleneck.
- **GPU entry-level** : la GTX 1650 mobile 4GB est déjà en tension sur
  Roblox même en mode Auto. Les flags qualité ne changent presque rien.
- Les modes Perf ne peuvent pas améliorer un CPU qui plafonne.

### Recommandations données
- Limiter les FPS à 60 (au lieu de 120+) sur laptop de cette génération.
- Vérifier dans les paramètres Windows (batterie → performances élevées)
  que le GPU dédié (GTX 1650) est bien activé et pas l'Intel UHD intégré.

## "Rejoindre un joueur" qui marche pas + nouvelle feature "Amis en ligne" (08/09)

### Contexte
L'utilisateur essayait de rejoindre un ami via son pseudo ("Rejoindre un
joueur"), souvent quand lui-même était déjà en jeu (ex. Blox Fruits) :
résultat "introuvable" ou ça ne le mettait pas en jeu. Il a aussi demandé
une option pour voir qui parmi ses amis est connecté et les rejoindre
depuis le compte sélectionné.

### Cause
1. **BUG RACINE — mauvais nom de champ** : l'API présence Roblox renvoie
   `userPresenceType` (avec "user" devant) et PAS `presenceType`. L'ancien
   code lisait `presenceType` -> champ toujours absent (None) -> TOUT le
   monde était classé "offline", même les joueurs en partie. D'où :
   - join by player : "introuvable"/"pas en jeu" même quand le joueur
     était sur Blox Fruits ;
   - la future liste d'amis ne montrerait jamais personne en ligne (0
     résultat = "ça load pas les friends").
2. **Endpoint friends inexistant** : `friends.roblox.com/v1/my/friends`
   renvoie 404 -> il faut `friends.roblox.com/v1/users/{userId}/friends`.
   Cet endpoint donne les IDs mais AVEC les noms VIDES -> il faut en plus
   un batch `users.roblox.com/v1/users` (POST) pour les usernames.
3. **Rate-limit** : l'endpoint usernames (`/v1/usernames/users`) est très
   sensible au 429 quand on enchaîne des requêtes.

### Fix (partie 1 — "Rejoindre un joueur")
- Parsing corrigé : `ptype = p.get("userPresenceType", p.get("presenceType", 0))`;
  valeurs 0=offline, 1=online, 2=in_game (avec placeId/gameId/lastLocation).
- `accounts.get_player_presence(user_id, token=None)` accepte le
  `.ROBLOSECURITY` du compte sélectionné -> présence fiable (même serveur).
- Refactor présence : `accounts.get_users_presence(user_ids, token=)` en
  batch (jusqu'à ~100 IDs en 1 appel) -> `{user_id: {status, place_id,
  job_id, last}}`. `get_player_presence` n'est plus qu'un wrapper.
- `resolve_player` loggue le code HTTP (`_last_resolve_status`).
- Fallback : si `resolve_player` échoue (même sur 429), on cherche le
  pseudo dans la liste d'amis du compte sélectionné (match name/displayName
  insensible à la casse).

### nouvelle feature (partie 2 — "Amis en ligne")
- `accounts.get_friends(user_id, token)` : GET
  `friends.roblox.com/v1/users/{user_id}/friends` (pagination limit=100)
  -> IDs ; puis noms via `_get_users_batch` (POST `/v1/users`, lots de 100,
  retry sur 429). Retour : liste [{id, name, display}].
- Carte "AMIS EN LIGNE" dans la vue Comptes (sous "Rejoindre un joueur") :
  bouton "⟳ Charger" -> charge les amis du compte sélectionné + leur
  présence batch -> liste des amis en ligne (🟢 online / 🎮 en jeu) avec
  bouton ▶ pour rejoindre (même serveur si gameId, sinon serveur public).
- Compteur dans le header de la carte ; displayName affiché, pseudo entre
  parenthèses quand il diffère.

### UI compactée (retour des "jeux récents" disparus)
La vue Comptes débordait verticalement : en ajoutant la carte Amis,
la section "Jeux récents" sortait de l'écran (l'utilisateur croyait que
les vieux jeux avaient été supprimés). Fix :
- panneau droit transformé en CTkScrollableFrame ;
- carte "Choisir un jeu" réduite (aperçu 64px au lieu de 120, bouton
  Rejoindre 36px, texte minimal) ;
- `rec_grid` en hauteur fixe (expand retiré) -> tout redevient visible.

### Validation
- Code source uniquement (pas encore push GitHub / Bureau / exe).
- Testé en local avec les vrais comptes : 52 amis récupérés, 2 en ligne
  détectés in_game (Avant-gardes d'anime, Dernier arrêt) avec gameId +
  placeId -> le join même serveur est possible.
- Reste à tester sur PC par l'utilisateur : clic "⟳ Charger", affichage
  des en-ligne, join par ami, et join par pseudo confirmé.

### Évolutions demandées par l'utilisateur (même jour)
1. **Layout** : les 2 cartes (Amis / Jeux récents) étaient empilées,
   il fallait scroller pour voir les jeux récents. Passé en côte à côte,
   puis (toujours jugé chelou) en **CTkTabview unique** avec 2 onglets
   "AMIS EN LIGNE" / "JEUX RÉCENTS" (hauteur 230) — plus de scroll.
2. **Amis H24** : plus besoin de cliquer "Charger". `load_friends(auto=True)`
   est déclenché :
   - à l'ouverture de la vue Comptes (`show_view`) ;
   - à chaque changement de compte (`select_account`) ;
   - toutes les 60 s (`friends_refresh_loop`, seulement si la vue Comptes
     est affichée, contrôlé par `self._current_view`).
   Anti-rafale : skip si dernier load < 3 s (`_friends_last_load`).
   `_friends_thread` a été sécurisé (try/except global + logs `[Amis]`)
   pour ne jamais bloquer le bouton ni laisser une exception silencieuse.
   C'est légal : seules les API officielles Roblox sont utilisées
   (friends.roblox.com, presence.roblox.com, users.roblox.com) — les
   mêmes que le site roblox.com, avec le cookie du compte de l'utilisateur.

## 08/09 SUITE (3) - Tetes des amis en ligne + robustesse ~300 amis

### Contexte
L'utilisateur veut voir les AVATARS (tetes) des joueurs en ligne dans la
liste Amis, et tester avec son compte principal (~300 amis) pour verifier
que le chargement ne bugue pas.

### Problem / cause
- L'ancien code chargeait UN avatar a la fois (_avatar_for) : sur 300
  amis ce serait 300 requetes individuelles -> tres lent et risque de
  rate-limit.
- _get_users_batch faisait les requetes POST en SEQUENTIEL (1 lot de
  100 a la fois) : 312 IDs -> 14.7 s (!). Le goulot etait le nombre de
  requetes, pas le rate-limit.
- get_users_presence n'avait AUCUNE pagination : un POST unique avec
  300 userIds risquait 429 ou la troncature du body.

### Fix
1. **Tetes 32px par liste d'amis** (_avatars_map dans app_ui.py) :
   un seul appel batch vatar-headshot?userIds=a,b,c par lot de 100,
   puis telechargement des PNG en threads paralleles. Les tetes
   arrivent APRES le rendu (nom + statut immediats, visage ensuite),
   appliquees via self.ui().
2. **_get_users_batch parallellise** (accounts.py) : un thread par lot
   de 100, timeout 8 s, backoff 429 reduit a 1 s. 312 IDs : 14.7 s -> 0.2 s.
3. **get_users_presence pagine + parallellise** : un thread par lot de
   100 ; retour identique ({user_id: {status, place_id, job_id, last}}),
   appels existants inchanges. 52 presences en 0.2 s.

### Validation
- Qd parallele : 312 IDs resolus en 0.2 s (au lieu de 14.7 s), presence
  52 en 0.2 s.
- Rendu UI complet teste en local (tetes reellement chargees depuis le
  reseau) : OK.
- Reste a tester par l'utilisateur sur SON compte principal (~300 amis) :
  vitesse du chargement, affichage des tetes, absence de bug.

## Pseudos des amis affiches en ID au lieu du pseudo (08/09 SOIR)

### Contexte
L'utilisateur etait sur son alt et a bascule sur le compte principal : la
liste "Amis en ligne" affichait les IDs numeriques au lieu des pseudos.

### Cause
L'affichage du pseudo passait par le batch POST `users.roblox.com/v1/users`.
Cet endpoint est en rate-limit quasi permanent (HTTP 429), meme avec le
cookie `.ROBLOSECURITY` du compte. Quand il echouait, `_get_users_batch`
retournait vide et le fallback codait le pseudo = `str(id)` -> liste pleine
d'IDs.
Le GET individuel `users.roblox.com/v1/users/{id}`, lui, marche parfaitement
(test : "Roblox", "nvrlnd" resolus sans probleme).

### Fix
Deux changements :
1. **`accounts._get_users_individual(ids, token)`** : nouveau helper qui
   resout les pseudos par GET individuel un par un, en parallele (semaphore
   de 10, retry 429 avec backoff 0.6 s, 404/400 ignores). Fiable meme en
   plein rate-limit.
2. **`accounts.get_friends(..., resolve_names=True)`** : nouveau parametre.
   Si `False`, il ne renvoie que les IDs bruts (sans le batch 429) ->
   {id, name=ID, display=ID}.
3. **Flux des amis en ligne** (`_friends_thread` dans app_ui.py) : charge
   les IDs via `get_friends(resolve_names=False)` (rapide, pas de batch) ->
   presence batch -> ne resout les pseudos QUE des amis EN LIGNE via
   `_get_users_individual` (petit set de ~2-10, pas les 300) -> vrais
   display name + pseudo.

Le fallback de "Rejoindre un joueur" (resolve_player, ligne 946) garde
`get_friends(resolve_names=True)` par defaut, inchange.

### Validation (en local, compte principal)
- get_friends(resolve_names=False) : 52 IDs instantanement.
- presence batch : distingue bien l'ami en ligne.
- _get_users_individual : resout le pseudo « xXdevXx_34 | wolf_storm ».
- Syntaxe des deux fichiers verifiee : OK.
- A tester par l'utilisateur : bascule entre comptes -> pseudos reels
  affiches pour les amis en ligne.
