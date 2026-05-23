// Card System
export type CardType = 'character' | 'npc' | 'location' | 'secret' | 'memory' | 'knowledge' | 'organization' | 'plot_thread' | 'item' | 'lore' | 'plot_arc' | 'chapter_summary';
export const CARD_TYPES: CardType[] = ['character', 'npc', 'location', 'secret', 'memory', 'knowledge', 'organization', 'plot_thread', 'item', 'lore', 'plot_arc', 'chapter_summary'];

export type Importance = 'critical' | 'pov' | 'love_interest' | 'antagonist' | 'main' | 'recurring' | 'supporting' | 'important' | 'minor' | 'one_time' | 'background' | 'extra';
export const IMPORTANCE_LEVELS: Importance[] = ['critical', 'pov', 'love_interest', 'antagonist', 'main', 'recurring', 'supporting', 'important', 'minor', 'one_time', 'background', 'extra'];

// Character / NPC
export type CharacterRole = 'protagonist' | 'love_interest' | 'pov_character' | 'npc' | 'supporting' | 'antagonist' | 'minor';
export type RelationshipRole = 'friend' | 'enemy' | 'family' | 'rival' | 'love_interest' | 'professional' | 'ally';
export type TrustModifierType = 'slow_to_trust' | 'quick_to_trust' | 'volatile' | 'grudge_holder';
export type TrustModifierSeverity = 'mild' | 'moderate' | 'severe';
export type Archetype = 'POWER_HOLDER' | 'TRANSACTIONAL' | 'COMMON_PEOPLE' | 'OPPOSITION' | 'SPECIALIST' | 'PROTECTOR' | 'OUTSIDER';
export const ARCHETYPES: Archetype[] = ['POWER_HOLDER', 'TRANSACTIONAL', 'COMMON_PEOPLE', 'OPPOSITION', 'SPECIALIST', 'PROTECTOR', 'OUTSIDER'];
export type BehavioralModifier = 'OBSESSIVE' | 'SADISTIC' | 'PARANOID' | 'FANATICAL' | 'NARCISSISTIC' | 'SOCIOPATHIC' | 'ADDICTED' | 'HONOR_BOUND' | 'GRIEF_CONSUMED';
export const BEHAVIORAL_MODIFIERS: BehavioralModifier[] = ['OBSESSIVE', 'SADISTIC', 'PARANOID', 'FANATICAL', 'NARCISSISTIC', 'SOCIOPATHIC', 'ADDICTED', 'HONOR_BOUND', 'GRIEF_CONSUMED'];
export type CooperationDefault = 'minimal' | 'moderate' | 'significant' | 'major';
export type EscalationStyle = 'cautious' | 'standard' | 'aggressive' | 'explosive';

// Guidelines
export type PovMode = 'single' | 'dual';
export type NarrativeVoice = 'first' | 'third';
export type Tense = 'present' | 'past';
export type ScenePacing = 'slow' | 'moderate' | 'fast';
export const SCENE_PACING: ScenePacing[] = ['slow', 'moderate', 'fast'];
export type ResponseLength = 'short' | 'medium' | 'long' | 'variable';

// Memory
export type EmotionalTone = 'positive' | 'negative' | 'complex' | 'neutral' | 'traumatic';
export type MemoryImportance = 'critical' | 'high' | 'medium' | 'low';
export type MemoryCategory = 'first_encounter' | 'achievement' | 'intimate' | 'promise' | 'rescue' | 'reunion' | 'triumph' | 'betrayal' | 'loss' | 'threat' | 'deception' | 'humiliation' | 'farewell' | 'confession' | 'discovery' | 'revelation' | 'confrontation' | 'realization' | 'sacrifice';

// Secret
export type SecretCategory = 'identity' | 'crime' | 'betrayal' | 'origin' | 'relationship' | 'knowledge' | 'possession' | 'ability' | 'location' | 'plan';
export type DiscoveryRisk = 'low' | 'medium' | 'high' | 'critical';
export type Difficulty = 'trivial' | 'easy' | 'moderate' | 'hard' | 'nearly_impossible';
export type SecretSignificance = 'critical' | 'major' | 'minor';
export type RevelationPlanned = 'yes' | 'no' | 'maybe' | 'gradual';
export type SecretStatus = 'active' | 'partially_revealed' | 'fully_revealed' | 'resolved';

// Location
export type LocationCategory = 'city' | 'town' | 'village' | 'neighborhood' | 'district' | 'building' | 'room' | 'estate' | 'shop' | 'tavern' | 'temple' | 'fortress' | 'wilderness' | 'forest' | 'cave' | 'underground' | 'lake' | 'mountain' | 'landmark' | 'ship' | 'ruins' | 'dungeon' | 'hidden';
export type LocationAccess = 'public' | 'private' | 'restricted' | 'secret';
export type LocationStatus = 'active' | 'destroyed' | 'abandoned' | 'under_construction' | 'changing';

// Organization
export type OrgCategory = 'government' | 'military' | 'nobility' | 'council' | 'religion' | 'cult' | 'temple' | 'order' | 'guild' | 'merchant' | 'criminal' | 'syndicate' | 'faction' | 'secret_society' | 'rebellion' | 'alliance';
export type OrgStatus = 'active' | 'disbanded' | 'underground' | 'rising' | 'declining';
export type Influence = 'dominant' | 'major' | 'moderate' | 'minor' | 'negligible';
export type Scope = 'global' | 'national' | 'regional' | 'local' | 'cell-based';
export type PublicStance = 'respected' | 'feared' | 'hated' | 'unknown' | 'controversial';
export type LeadershipType = 'autocratic' | 'council' | 'democratic' | 'religious' | 'hereditary';
export type Hierarchy = 'rigid' | 'moderate' | 'loose' | 'flat';
export type OrgSize = 'massive' | 'large' | 'medium' | 'small' | 'handful';
export type Recruitment = 'open' | 'selective' | 'invitation' | 'hereditary' | 'secret';

// Plot Thread
export type ThreadPriority = 'plot_critical' | 'important' | 'background';
export type ThreadStatus = 'active' | 'dormant' | 'resolved';
export type ThreadPhase = 'emerging' | 'developing' | 'escalating' | 'climax' | 'resolving';
export type TrackingMode = 'counter' | 'time' | 'both';

// Plot Arc
export type ArcCategory = 'main_plot' | 'subplot' | 'side_quest' | 'character_arc' | 'relationship' | 'rivalry' | 'redemption' | 'mystery' | 'romance' | 'revenge' | 'survival' | 'political' | 'heist' | 'quest' | 'discovery';
export type ArcPriority = 'critical' | 'high' | 'medium' | 'low';
export type ArcStatus = 'planned' | 'active' | 'paused' | 'completed' | 'abandoned';
export type ArcPhase = 'setup' | 'rising_action' | 'climax' | 'resolution';

// Lore
export type LoreCategory = 'magic_system' | 'technology' | 'economy' | 'politics' | 'natural_law' | 'metaphysics' | 'prophecy' | 'cosmology' | 'afterlife';
export type LoreScope = 'universal' | 'world' | 'region' | 'local';
export type LoreImportance = 'critical' | 'high' | 'medium' | 'flavor';

// Knowledge
export type Confidence = 'certain' | 'strong' | 'moderate' | 'uncertain' | 'suspicion';

// Item
export type ItemCategory = 'weapon' | 'armor' | 'accessory' | 'tool' | 'consumable' | 'document' | 'key_item' | 'currency' | 'artifact' | 'clothing';
export type ItemRarity = 'common' | 'uncommon' | 'rare' | 'legendary' | 'unique';
export type ItemStatus = 'intact' | 'damaged' | 'destroyed' | 'lost' | 'hidden' | 'abandoned';

// Chapter
export type ChapterStatus = 'complete' | 'in_progress';
