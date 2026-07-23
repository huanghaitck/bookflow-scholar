import { useMemo, useState } from 'react';
import { createMockSnapshot } from '../bridge/MockBookflowBridge';
import type {
  FrontendPreferences,
  MascotCharacter,
  MascotForm,
  MascotSkin,
  WorkflowState,
} from '../domain/bookflow-contract';
import { createInitialPreferences } from '../state/frontend-preferences';
import { MascotHost, type MascotInteractionHooks } from './MascotHost';

const STATES: WorkflowState[] = [
  'idle',
  'thinking',
  'working',
  'reviewing',
  'completed',
  'warning',
  'error',
  'sleeping',
];

const VISUAL_SETS: ReadonlyArray<{
  value: string;
  character: MascotCharacter;
  skin: MascotSkin;
  label: string;
}> = [
  { value: 'editor-default', character: 'mascot_editor', skin: 'skin_default', label: 'Eleanor' },
  { value: 'editor-midnight', character: 'mascot_editor', skin: 'skin_midnight_archivist', label: 'Midnight Archivist' },
  { value: 'scholar-default', character: 'mascot_scholar', skin: 'skin_default', label: 'Clara' },
  { value: 'explorer-default', character: 'mascot_explorer', skin: 'skin_default', label: 'Stella' },
];

export function MascotRuntimeHarness() {
  const [snapshot, setSnapshot] = useState(createMockSnapshot);
  const [preferences, setPreferences] = useState<FrontendPreferences>(() =>
    createInitialPreferences({
      activeCharacter: 'mascot_editor',
      activeSkin: 'skin_default',
      mascotForm: 'chibi',
      mascotPosition: { x: 28, y: 82, dock: 'free' },
    }),
  );
  const [clicks, setClicks] = useState(0);
  const [doubleClicks, setDoubleClicks] = useState(0);
  const [dragStarts, setDragStarts] = useState(0);
  const [dragEnds, setDragEnds] = useState(0);
  const [dragCancels, setDragCancels] = useState(0);
  const [fallbacks, setFallbacks] = useState(0);
  const hooks = useMemo<MascotInteractionHooks>(() => ({
    onClick: () => setClicks((value) => value + 1),
    onDoubleClick: () => setDoubleClicks((value) => value + 1),
    onDragStart: () => setDragStarts((value) => value + 1),
    onDragEnd: () => setDragEnds((value) => value + 1),
    onDragCancel: () => setDragCancels((value) => value + 1),
    onFallback: () => setFallbacks((value) => value + 1),
  }), []);

  const visualSet = VISUAL_SETS.find(
    (item) => item.character === preferences.activeCharacter && item.skin === preferences.activeSkin,
  )?.value ?? 'editor-default';

  const selectState = (state: WorkflowState) => {
    setSnapshot((current) => ({
      ...current,
      workflowState: state,
      mascotState: state,
      updatedAt: new Date().toISOString(),
    }));
  };

  return (
    <main className="runtime-harness" data-theme="plum_editorial">
      <header>
        <span>S3 SERIAL VALIDATION HARNESS</span>
        <h1>Bookflow code-driven mascot runtime</h1>
        <p>64 frozen RGBA fallbacks · semantic state adapter · local effects · no backend claims</p>
      </header>
      <section className="runtime-controls" aria-label="Runtime controls">
        <label>
          State
          <select
            data-testid="runtime-state"
            value={snapshot.workflowState}
            onChange={(event) => selectState(event.target.value as WorkflowState)}
          >
            {STATES.map((state) => <option key={state}>{state}</option>)}
          </select>
        </label>
        <label>
          Visual set
          <select
            data-testid="runtime-visual-set"
            value={visualSet}
            onChange={(event) => {
              const selected = VISUAL_SETS.find((item) => item.value === event.target.value)!;
              setPreferences((current) => ({
                ...current,
                activeCharacter: selected.character,
                activeSkin: selected.skin,
              }));
            }}
          >
            {VISUAL_SETS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label>
          Form
          <select
            data-testid="runtime-form"
            value={preferences.mascotForm}
            onChange={(event) => setPreferences((current) => ({
              ...current,
              mascotForm: event.target.value as MascotForm,
            }))}
          >
            <option value="full">full</option>
            <option value="chibi">chibi</option>
          </select>
        </label>
        <label>
          Motion
          <select
            data-testid="runtime-motion"
            value={preferences.ambientMotion}
            onChange={(event) => setPreferences((current) => ({
              ...current,
              ambientMotion: event.target.value as FrontendPreferences['ambientMotion'],
            }))}
          >
            <option value="full">full</option>
            <option value="reduced">reduced</option>
            <option value="off">off</option>
          </select>
        </label>
        <label className="runtime-check">
          <input
            type="checkbox"
            data-testid="runtime-reduced-override"
            checked={preferences.reducedMotion}
            onChange={(event) => setPreferences((current) => ({
              ...current,
              reducedMotion: event.target.checked,
            }))}
          />
          OS/user reduced override
        </label>
      </section>
      <section className="runtime-stage" data-testid="runtime-stage">
        <MascotHost
          snapshot={snapshot}
          preferences={preferences}
          setPreferences={setPreferences}
          persistenceKey={null}
          hooks={hooks}
        />
      </section>
      <footer className="runtime-counters" aria-live="polite">
        <span data-testid="runtime-clicks">clicks:{clicks}</span>
        <span data-testid="runtime-double-clicks">double:{doubleClicks}</span>
        <span data-testid="runtime-drag-starts">drag-start:{dragStarts}</span>
        <span data-testid="runtime-drag-ends">drag-end:{dragEnds}</span>
        <span data-testid="runtime-drag-cancels">drag-cancel:{dragCancels}</span>
        <span data-testid="runtime-fallbacks">fallbacks:{fallbacks}</span>
      </footer>
    </main>
  );
}
