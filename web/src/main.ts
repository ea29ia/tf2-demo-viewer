// Thin entry point. All viewer logic lives in ./viewer/* modules.
//
// Import order matters: `./viewer/playback` pulls in the whole module graph,
// including inspect.ts, whose tap-to-inspect pointerdown handler registers at
// module scope. It must be registered BEFORE setupInput() below attaches the
// look/pinch handlers (the tap-validity check reads activePointers.size).

import './styles.css';
import { setupInput } from './viewer/input';
import { togglePlayOrRestart, boot, animate } from './viewer/playback';
import { setMode, updateHud, updateModeButtons } from './viewer/ui';

setupInput({
  togglePlay: togglePlayOrRestart,
  setMode,
  updateHud,
  updateModeButtons,
});
boot();
animate();
