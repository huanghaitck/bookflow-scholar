import { setWasmUrl } from '@lottiefiles/dotlottie-react';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MascotRuntimeHarness } from './MascotRuntimeHarness';
import '../r1-app.css';
import './runtime-harness.css';

setWasmUrl('/wasm/dotlottie-player.wasm');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MascotRuntimeHarness />
  </StrictMode>,
);
