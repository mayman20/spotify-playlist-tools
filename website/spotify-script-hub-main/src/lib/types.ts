export interface SpotifyUser {
  id: string;
  displayName: string;
  email: string;
  avatarUrl: string;
  playlists: number;
  savedTracks: number;
  following: number;
}

export interface ConfigField {
  name: string;
  label: string;
  type: 'select' | 'number' | 'toggle' | 'text';
  options?: string[];
  placeholder?: string;
  defaultValue?: string | number | boolean;
}

export interface Script {
  id: string;
  name: string;
  description: string;
  icon: string;
  configFields: ConfigField[];
  enabled?: boolean;
  disabledReason?: string;
}

export interface Run {
  id: string;
  scriptId: string;
  scriptName: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  startedAt: string;
  duration: string;
  logsPreview: string;
}
