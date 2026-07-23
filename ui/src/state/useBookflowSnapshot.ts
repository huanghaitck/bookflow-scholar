import { useEffect, useState } from 'react';
import { MockBookflowBridge } from '../bridge/MockBookflowBridge';
import { BookflowBridgeRouter } from '../bridge/BookflowBridgeRouter';
import type { BookflowBridge } from '../bridge/BookflowBridge';
import { MOCK_SCENARIO_IDS } from '../bridge/MockBridgeClient';
import type { MockScenarioId } from '../contracts/generated/backend-v1_2';
import type { BookflowSnapshot } from '../domain/bookflow-contract';
import { createDisconnectedSnapshot } from './initialBookflowSnapshot';

export const bookflowBridge = new BookflowBridgeRouter(new MockBookflowBridge());

export function configureBookflowBridge(bridge: BookflowBridge): void {
  bookflowBridge.setBridge(bridge);
}

export function useBookflowSnapshot(
  snapshotOverride?: BookflowSnapshot,
): BookflowSnapshot {
  const [snapshot, setSnapshot] = useState<BookflowSnapshot>(
    snapshotOverride ?? createDisconnectedSnapshot,
  );

  useEffect(() => {
    if (snapshotOverride) {
      setSnapshot(snapshotOverride);
      return undefined;
    }

    let active = true;
    const resync = async () => {
      const envelope = await bookflowBridge.getCompleteSnapshot();
      if (active) setSnapshot(envelope.data);
    };
    const unsubscribe = bookflowBridge.subscribe((event) => {
      if (event.type === 'snapshot.replaced') {
        setSnapshot(event.snapshot);
      } else {
        void resync();
      }
    });
    const initialize = async () => {
      const requested = typeof window === 'undefined'
        ? null
        : new URLSearchParams(window.location.search).get('mockScenario');
      const activeBridge = bookflowBridge.getBridge();
      if (
        activeBridge instanceof MockBookflowBridge
        && requested
        && (MOCK_SCENARIO_IDS as readonly string[]).includes(requested)
      ) {
        await activeBridge.setMockScenario(requested as MockScenarioId);
      } else {
        await bookflowBridge.connect();
      }
      await resync();
      const pending = typeof window === 'undefined'
        ? null
        : new URLSearchParams(window.location.search).get('commandPending');
      if (pending) {
        const current = (await bookflowBridge.getCompleteSnapshot()).data;
        if (activeBridge instanceof MockBookflowBridge) {
          activeBridge.replaceSnapshot({ ...current, commandPending: pending });
        }
      }
    };
    void initialize();
    return () => {
      active = false;
      unsubscribe();
    };
  }, [snapshotOverride]);

  return snapshot;
}
