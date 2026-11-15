import { http, HttpResponse } from 'msw'

/**
 * MSW handlers for /api/v1/materials/* endpoints
 * These handlers mock the Prefect-dependent material library APIs for E2E testing
 */

/**
 * Response types matching backend Pydantic models
 */
interface MaterialUploadResponse {
  novel_id: number
  title: string
  job_id: number
  status: string
  message: string
}

interface MaterialListItem {
  id: number
  title: string
  author: string | null
  synopsis: string | null
  created_at: string
  updated_at: string
  status: string | null
  chapters_count: number
}

interface MaterialDetailResponse {
  id: number
  title: string
  author: string | null
  synopsis: string | null
  source_meta: Record<string, unknown> | null
  status: string | null
  created_at: string
  updated_at: string
  chapters_count: number
  characters_count: number
  story_lines_count: number
  golden_fingers_count: number
  has_world_view: boolean
}

interface CharacterListItem {
  id: number
  name: string
  aliases: string[] | null
  description: string | null
  archetype: string | null
  first_appearance_chapter_id: number | null
}

interface MaterialImportResponse {
  file_id: string
  title: string
  folder_name: string
  file_type: string
}

/**
 * Mock data for testing
 */
const mockTimestamp = '2024-01-15T10:30:00Z'

const mockNovels: MaterialListItem[] = [
  {
    id: 1,
    title: 'Completed Novel',
    author: 'Test Author',
    synopsis: 'A completed novel for testing',
    created_at: mockTimestamp,
    updated_at: mockTimestamp,
    status: 'completed',
    chapters_count: 20,
  },
  {
    id: 2,
    title: 'Failed Novel',
    author: 'Test Author',
    synopsis: 'A failed novel for testing',
    created_at: mockTimestamp,
    updated_at: mockTimestamp,
    status: 'failed',
    chapters_count: 0,
  },
  {
    id: 3,
    title: 'Processing Novel',
    author: 'Test Author',
    synopsis: 'A processing novel for testing',
    created_at: mockTimestamp,
    updated_at: mockTimestamp,
    status: 'processing',
    chapters_count: 5,
  },
]

const mockCharacters: CharacterListItem[] = [
  {
    id: 1,
    name: 'Hero',
    aliases: ['The Protagonist', 'Chosen One'],
    description: 'The main protagonist of the story',
    archetype: 'Hero',
    first_appearance_chapter_id: 1,
  },
  {
    id: 2,
    name: 'Villain',
    aliases: ['The Antagonist', 'Dark Lord'],
    description: 'The main antagonist opposing the hero',
    archetype: 'Shadow',
    first_appearance_chapter_id: 3,
  },
  {
    id: 3,
    name: 'Mentor',
    aliases: null,
    description: 'The wise guide who helps the hero',
    archetype: 'Mentor',
    first_appearance_chapter_id: 2,
  },
]

/**
 * MSW handler for POST /api/v1/materials/upload
 * Returns immediately completed status (skips Prefect processing)
 */
export const mockMaterialsUploadHandler = http.post('/api/v1/materials/upload', async () => {
  const response: MaterialUploadResponse = {
    novel_id: 999,
    title: 'Test Novel',
    job_id: 1001,
    status: 'pending',
    message: 'Novel upload successful, decomposition started',
  }
  return HttpResponse.json(response)
})

/**
 * MSW handler for GET /api/v1/materials/list
 * Returns test material list
 */
export const mockMaterialsListHandler = http.get('/api/v1/materials/list', () => {
  return HttpResponse.json(mockNovels)
})

/**
 * MSW handler for GET /api/v1/materials/:novelId
 * Returns material detail with entity counts
 */
export const mockMaterialsDetailHandler = http.get(
  '/api/v1/materials/:novelId',
  ({ params }) => {
    const novelId = Number(params.novelId)
    const response: MaterialDetailResponse = {
      id: novelId,
      title: 'Test Novel',
      author: 'Test Author',
      synopsis: 'A test novel for E2E testing',
      source_meta: {
        file_path: '/uploads/test_novel.txt',
        file_size: 1024000,
        original_filename: 'test_novel.txt',
      },
      status: 'completed',
      created_at: mockTimestamp,
      updated_at: mockTimestamp,
      chapters_count: 10,
      characters_count: 5,
      story_lines_count: 3,
      golden_fingers_count: 2,
      has_world_view: true,
    }
    return HttpResponse.json(response)
  }
)

/**
 * MSW handler for GET /api/v1/materials/:novelId/characters
 * Returns character entity list
 */
export const mockMaterialsCharactersHandler = http.get(
  '/api/v1/materials/:novelId/characters',
  () => {
    return HttpResponse.json(mockCharacters)
  }
)

/**
 * MSW handler for POST /api/v1/materials/import
 * Returns import confirmation
 */
export const mockMaterialsImportHandler = http.post('/api/v1/materials/import', () => {
  const response: MaterialImportResponse = {
    file_id: 'new-file-id-12345',
    title: 'Hero-Reference',
    folder_name: 'Characters',
    file_type: 'character',
  }
  return HttpResponse.json(response)
})

/**
 * MSW handler for GET /api/v1/materials/:novelId/tree
 * Returns file tree structure for material library
 */
export const mockMaterialsTreeHandler = http.get(
  '/api/v1/materials/:novelId/tree',
  () => {
    return HttpResponse.json({
      tree: [
        {
          id: 1,
          type: 'chapter',
          title: 'Chapter 1: The Beginning',
          metadata: {
            chapter_number: 1,
            summary: 'Introduction to the story',
            plots_count: 3,
            created_at: mockTimestamp,
          },
        },
        {
          id: 2,
          type: 'chapter',
          title: 'Chapter 2: The Journey',
          metadata: {
            chapter_number: 2,
            summary: 'The hero begins their journey',
            plots_count: 2,
            created_at: mockTimestamp,
          },
        },
      ],
    })
  }
)

/**
 * MSW handler for GET /api/v1/materials/:novelId/storylines
 * Returns storyline list
 */
export const mockMaterialsStorylinesHandler = http.get(
  '/api/v1/materials/:novelId/storylines',
  () => {
    return HttpResponse.json([
      {
        id: 1,
        novel_id: 1,
        title: 'Main Plot',
        description: 'The primary storyline',
        main_characters: ['Hero', 'Villain'],
        themes: ['Redemption', 'Courage'],
        stories_count: 5,
        created_at: mockTimestamp,
      },
      {
        id: 2,
        novel_id: 1,
        title: 'Romance Subplot',
        description: 'The romantic storyline',
        main_characters: ['Hero', 'Love Interest'],
        themes: ['Love', 'Sacrifice'],
        stories_count: 3,
        created_at: mockTimestamp,
      },
    ])
  }
)

/**
 * MSW handler for GET /api/v1/materials/:novelId/worldview
 * Returns world view settings
 */
export const mockMaterialsWorldviewHandler = http.get(
  '/api/v1/materials/:novelId/worldview',
  () => {
    return HttpResponse.json({
      id: 1,
      novel_id: 1,
      power_system: 'Cultivation System with Nine Realms',
      world_structure: 'Three Domains: Mortal, Immortal, Divine',
      key_factions: [
        { name: 'Righteous Sect' },
        { name: 'Demonic Cult' },
        { name: 'Neutral Alliance' },
      ],
      special_rules: 'No killing within sect grounds',
      created_at: mockTimestamp,
      updated_at: mockTimestamp,
    })
  }
)

/**
 * MSW handler for GET /api/v1/materials/:novelId/goldenfingers
 * Returns golden fingers (special abilities) list
 */
export const mockMaterialsGoldenfingersHandler = http.get(
  '/api/v1/materials/:novelId/goldenfingers',
  () => {
    return HttpResponse.json([
      {
        id: 1,
        novel_id: 1,
        name: 'Ancient Ring',
        type: 'Artifact',
        description: 'A mysterious ring containing an ancient soul',
        first_appearance_chapter_id: 1,
        evolution_history: [
          { chapter: 1, stage: 'Awakened' },
          { chapter: 5, stage: 'Level 2' },
          { chapter: 10, stage: 'Level 3' },
        ],
        created_at: mockTimestamp,
      },
    ])
  }
)

/**
 * MSW handler for GET /api/v1/materials/:novelId/relationships
 * Returns character relationships
 */
export const mockMaterialsRelationshipsHandler = http.get(
  '/api/v1/materials/:novelId/relationships',
  () => {
    return HttpResponse.json([
      {
        id: 1,
        character_a_id: 1,
        character_a_name: 'Hero',
        character_b_id: 2,
        character_b_name: 'Villain',
        relationship_type: 'Enemy',
        sentiment: 'Hostile',
        description: 'Sworn enemies since childhood',
      },
      {
        id: 2,
        character_a_id: 1,
        character_a_name: 'Hero',
        character_b_id: 3,
        character_b_name: 'Mentor',
        relationship_type: 'Student-Teacher',
        sentiment: 'Respectful',
        description: 'Master and disciple relationship',
      },
    ])
  }
)

/**
 * MSW handler for DELETE /api/v1/materials/:novelId
 * Returns delete confirmation
 */
export const mockMaterialsDeleteHandler = http.delete(
  '/api/v1/materials/:novelId',
  () => {
    return HttpResponse.json({ message: 'Material library deleted successfully' })
  }
)

/**
 * MSW handler for POST /api/v1/materials/:novelId/retry
 * Returns retry confirmation
 */
export const mockMaterialsRetryHandler = http.post(
  '/api/v1/materials/:novelId/retry',
  () => {
    return HttpResponse.json({
      message: 'Retry started successfully',
      job_id: 1002,
      status: 'pending',
    })
  }
)

/**
 * All MSW handlers for materials endpoints
 */
export const materialsHandlers = [
  mockMaterialsUploadHandler,
  mockMaterialsListHandler,
  mockMaterialsDetailHandler,
  mockMaterialsCharactersHandler,
  mockMaterialsImportHandler,
  mockMaterialsTreeHandler,
  mockMaterialsStorylinesHandler,
  mockMaterialsWorldviewHandler,
  mockMaterialsGoldenfingersHandler,
  mockMaterialsRelationshipsHandler,
  mockMaterialsDeleteHandler,
  mockMaterialsRetryHandler,
]
