import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { setWasmUrl } from '@lottiefiles/dotlottie-react';
import { R1App } from './R1App';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { ContractBookflowBridge } from './bridge/ContractBookflowBridge';
import { TauriBridgeClientStub } from './bridge/TauriBridgeClient_stub';
import { configureBookflowBridge } from './state/useBookflowSnapshot';
import { createDisconnectedSnapshot } from './state/initialBookflowSnapshot';
import './r1-app.css';

setWasmUrl('/wasm/dotlottie-player.wasm');

const transport = {
  invoke: <T,>(command: string, args: Record<string, unknown>) => invoke<T>(command, args),
  listen: async <T,>(event: string, listener: (payload: T) => void) => {
    const unlisten = await listen<T>(event, (message) => listener(message.payload));
    return unlisten;
  },
};

configureBookflowBridge(new ContractBookflowBridge(
  new TauriBridgeClientStub(transport),
  createDisconnectedSnapshot(),
));

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <R1App />
  </StrictMode>,
);
