import React, { useState } from 'react';
import Sidebar from './Sidebar';
import Dashboard from './views/Dashboard';
import Jumps from './views/Jumps';
import MyRig from './views/MyRig';
import Inventory from './views/Inventory';
import Dropzones from './views/Dropzones';
import Settings from './views/Settings';

const VIEWS = {
  dashboard: Dashboard,
  jumps: Jumps,
  myrig: MyRig,
  inventory: Inventory,
  dropzones: Dropzones,
  settings: Settings,
};

export default function App() {
  // App opens on the Dashboard — the new landing page hosts a
  // function bar (Log jump shortcut) and a configurable grid of
  // stats widgets. Identity moved into Settings.
  const [activeTab, setActiveTab] = useState('dashboard');
  const View = VIEWS[activeTab] || Dashboard;

  return (
    <div className="min-h-screen flex" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />
      <main className="flex-1 min-w-0 overflow-x-hidden">
        <View />
      </main>
    </div>
  );
}
