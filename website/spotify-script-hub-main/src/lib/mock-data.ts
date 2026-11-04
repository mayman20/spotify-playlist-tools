import { Script } from './types';

export const scripts: Script[] = [
  {
    id: 'monthly-recommendations',
    name: 'Monthly Recommendations Playlist',
    description: 'Discover tracks through playlists that overlap with your recent listening.',
    icon: 'Star',
    enabled: false,
    disabledReason: 'Run the monthly recommender from the command line. Scheduled runs are managed on the server.',
    configFields: [
    ],
  },
  {
    id: 'liked-songs-mirror',
    name: 'Liked Songs Mirror',
    description: 'Mirror your liked songs into a private playlist.',
    icon: 'Heart',
    configFields: [
      { name: 'targetPlaylist', label: 'Target Playlist', type: 'select', options: ['Create New', 'Liked Songs Mirror', 'My Collection'] },
    ],
  },
  {
    id: 'archive-old-playlists',
    name: 'Archive Old Playlists',
    description: 'Move playlists older than X months into an Archive folder.',
    icon: 'Archive',
    enabled: false,
    disabledReason: 'Planned feature; Spotify playlist folder management is not supported by the API.',
    configFields: [
    ],
  },
  {
    id: 'vaulted-add',
    name: 'Vaulted Add',
    description: 'Build a Vaulted playlist from your library playlists, including songs that are not liked.',
    icon: 'Lock',
    configFields: [
      { name: 'targetPlaylist', label: 'Vault Playlist', type: 'select', options: ['Create New', 'The Vault', 'Favorites Vault'] },
    ],
  },
];
