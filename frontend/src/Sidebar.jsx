import React from 'react';
import { BookOpen, Layers, Boxes, MapPin, User, Settings as SettingsIcon } from 'lucide-react';

const NAV = [
  { id: 'profile', icon: User, label: 'Profile' },
  { id: 'jumps', icon: BookOpen, label: 'Jumps' },
  { id: 'myrig', icon: Layers, label: 'My rig' },
  { id: 'inventory', icon: Boxes, label: 'Inventory' },
  { id: 'dropzones', icon: MapPin, label: 'Dropzones' },
];

export default function Sidebar({ activeTab, setActiveTab }) {
  return (
    <aside
      className="w-56 flex flex-col h-screen sticky top-0"
      style={{ background: 'var(--surface-1)', borderRight: '0.5px solid var(--border)' }}
    >
      <div className="px-5 pt-7 pb-5" style={{ borderBottom: '0.5px solid var(--border)' }}>
        <div
          className="text-[10px] tracking-[0.3em] uppercase font-medium"
          style={{ color: 'var(--text-faint)' }}
        >
          Skydive Logbook
        </div>
        <div
          className="text-[15px] font-medium mt-1 tracking-tight"
          style={{ color: 'var(--text)' }}
        >
          Good morning, Alex
        </div>
      </div>

      <nav className="px-3 pt-4 flex flex-col gap-0.5">
        {NAV.map((t) => {
          const active = activeTab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] font-medium transition relative"
              style={{
                background: active ? 'var(--surface-3)' : 'transparent',
                color: active ? 'var(--text)' : 'var(--text-muted)',
              }}
              onMouseEnter={(e) => {
                if (!active) {
                  e.currentTarget.style.background = 'var(--surface-2)';
                  e.currentTarget.style.color = 'var(--text)';
                }
              }}
              onMouseLeave={(e) => {
                if (!active) {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = 'var(--text-muted)';
                }
              }}
            >
              {active && (
                <span
                  aria-hidden="true"
                  style={{
                    position: 'absolute',
                    left: -10, top: 8, bottom: 8,
                    width: 2, background: 'var(--accent)', borderRadius: 2,
                  }}
                />
              )}
              <t.icon className="w-4 h-4" strokeWidth={active ? 2 : 1.8} />
              <span>{t.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto p-3" style={{ borderTop: '0.5px solid var(--border)' }}>
        <button
          onClick={() => setActiveTab('settings')}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] font-medium transition"
          style={{
            background: activeTab === 'settings' ? 'var(--surface-3)' : 'transparent',
            color: activeTab === 'settings' ? 'var(--text)' : 'var(--text-muted)',
          }}
        >
          <SettingsIcon className="w-4 h-4" strokeWidth={1.8} />
          <span>Settings</span>
        </button>
      </div>
    </aside>
  );
}
