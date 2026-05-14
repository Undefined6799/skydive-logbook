import React, { useState } from 'react';
import Sidebar from './Sidebar';
import Profile from './views/Profile';
import Jumps from './views/Jumps';
import MyRig from './views/MyRig';
import Inventory from './views/Inventory';
import Dropzones from './views/Dropzones';
import Settings from './views/Settings';

const VIEWS = {
  profile: Profile,
  jumps: Jumps,
  myrig: MyRig,
  inventory: Inventory,
  dropzones: Dropzones,
  settings: Settings,
};

export default function App() {
  // P.2: app opens on the Profile tab. The view is the user's
  // landing page — surfaces their identity + career stats first.
  const [activeTab, setActiveTab] = useState('profile');
  const View = VIEWS[activeTab] || Profile;

  return (
    <div className="min-h-screen flex" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />
      <main className="flex-1 min-w-0 overflow-x-hidden">
        <View />
      </main>
    </div>
  );
}
