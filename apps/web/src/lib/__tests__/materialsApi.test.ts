/**
 * Tests for Materials API client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  materialsApi,
  type MaterialNovel,
  type MaterialChapter,
  type MaterialCharacter,
  type MaterialStory,
  type MaterialPlot,
  type MaterialStoryLine,
  type MaterialCharacterRelationship,
  type MaterialGoldenFinger,
  type MaterialWorldView,
  type MaterialTreeNode,
  type LibrarySummaryItem,
  type MaterialEntityType,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  type MaterialEntityItem,
  type MaterialPreviewResponse,
  type MaterialImportRequest,
  type MaterialImportResponse,
  type MaterialUploadResponse,
  type MaterialSearchResult,
  type BatchImportItem,
  type BatchImportResponse,
  type Faction,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  type EvolutionHistoryItem,
} from '../materialsApi'

// Mock the api client from apiClient
const mockApiGet = vi.fn()
const mockApiPost = vi.fn()
const mockApiDelete = vi.fn()

vi.mock('../apiClient', async (importOriginal) => ({
  apiErrorFromPayload: (await importOriginal<typeof import('../apiClient')>()).apiErrorFromPayload,
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
    delete: (...args: unknown[]) => mockApiDelete(...args),
  },
  getApiBase: vi.fn(() => 'http://localhost:8000'),
  getAccessToken: vi.fn(() => 'test-token'),
  tryRefreshToken: vi.fn(() => Promise.resolve(true)),
  resolveOwnedAuthSession: vi.fn(() => ({ accessToken: localStorage.getItem('access_token'), refreshToken: localStorage.getItem('refresh_token') })),
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
      public details?: Record<string, unknown>,
    ) {
      super(message)
      this.name = 'ApiError'
    }
  },
}))

// Mock fetch for upload function
global.fetch = vi.fn()

describe('materialsApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('refresh_token', 'test-refresh')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('list', () => {
    it('returns list of material novels', async () => {
      const mockNovels: MaterialNovel[] = [
        {
          id: 'novel-1',
          user_id: 'user-1',
          title: 'Test Novel',
          original_filename: 'test.txt',
          file_size: 1024,
          status: 'completed',
          total_chapters: 10,
          chapters_count: 10,
          total_characters: 5,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        },
        {
          id: 'novel-2',
          user_id: 'user-1',
          title: 'Another Novel',
          original_filename: 'another.txt',
          file_size: 2048,
          status: 'processing',
          created_at: '2024-01-02T00:00:00Z',
          updated_at: '2024-01-02T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockNovels)

      const result = await materialsApi.list()

      expect(result).toEqual(mockNovels)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/list')
    })

    it('returns empty array when no materials', async () => {
      mockApiGet.mockResolvedValue([])

      const result = await materialsApi.list()

      expect(result).toEqual([])
    })

    it('propagates API errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Server error'))

      await expect(materialsApi.list()).rejects.toThrow('Server error')
    })
  })

  describe('get', () => {
    it('returns material novel details', async () => {
      const mockNovel: MaterialNovel = {
        id: 'novel-1',
        user_id: 'user-1',
        title: 'Test Novel',
        original_filename: 'test.txt',
        file_size: 1024,
        status: 'completed',
        total_chapters: 10,
        chapters_count: 10,
        total_characters: 5,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      }
      mockApiGet.mockResolvedValue(mockNovel)

      const result = await materialsApi.get('novel-1')

      expect(result).toEqual(mockNovel)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1')
    })

    it('propagates API errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Not found'))

      await expect(materialsApi.get('novel-1')).rejects.toThrow('Not found')
    })
  })

  describe('upload', () => {
    const mockFile = new File(['test content'], 'test.txt', { type: 'text/plain' })

    it('uploads file successfully', async () => {
      const mockResponse: MaterialUploadResponse = {
        novel_id: 'novel-1',
        message: 'Upload successful',
        status: 'pending',
      }

      vi.mocked(global.fetch).mockResolvedValue({
        ok: true,
        json: async () => mockResponse,
      } as Response)

      const result = await materialsApi.upload(mockFile)

      expect(result).toEqual(mockResponse)
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/materials/upload'),
        expect.objectContaining({
          method: 'POST',
          headers: { Authorization: 'Bearer test-token' },
          body: expect.any(FormData),
        })
      )
    })

    it('uploads file with custom title', async () => {
      const mockResponse: MaterialUploadResponse = {
        novel_id: 'novel-1',
        message: 'Upload successful',
        status: 'pending',
      }

      vi.mocked(global.fetch).mockResolvedValue({
        ok: true,
        json: async () => mockResponse,
      } as Response)

      await materialsApi.upload(mockFile, 'Custom Title')

      const callArgs = vi.mocked(global.fetch).mock.calls[0]
      expect(callArgs[0]).toContain('/api/v1/materials/upload?title=Custom+Title')
      const formData = callArgs[1]?.body as FormData

      expect(formData.get('file')).toBe(mockFile)
    })

    it('handles 401 with token refresh', async () => {
      const { tryRefreshToken } = await import('../apiClient')
      const mockTryRefreshToken = tryRefreshToken as ReturnType<typeof vi.fn>

      const mockResponse: MaterialUploadResponse = {
        novel_id: 'novel-1',
        message: 'Upload successful',
        status: 'pending',
      }

      vi.mocked(global.fetch)
        .mockResolvedValueOnce({
          ok: false,
          status: 401,
          json: async () => ({ detail: 'Unauthorized' }),
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: async () => mockResponse,
        } as Response)

      mockTryRefreshToken.mockImplementation(async () => {
        localStorage.setItem('access_token', 'rotated-test-token')
        localStorage.setItem('refresh_token', 'rotated-test-refresh')
        return true
      })

      const result = await materialsApi.upload(mockFile)

      expect(result).toEqual(mockResponse)
      expect(mockTryRefreshToken).toHaveBeenCalled()
      expect(global.fetch).toHaveBeenCalledTimes(2)
    })

    it('throws ApiError on upload failure', async () => {
      vi.mocked(global.fetch).mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({ detail: 'Invalid file format' }),
      } as Response)

      await expect(materialsApi.upload(mockFile)).rejects.toThrow('Invalid file format')
    })

    it('keeps the counted size the server sends with an over-limit rejection', async () => {
      vi.mocked(global.fetch).mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({
          error_code: 'ERR_FILE_CONTENT_TOO_LONG',
          error_detail: { char_count: 312000, limit: 300000, trial_chapters: 20 },
        }),
      } as Response)

      await expect(materialsApi.upload(mockFile)).rejects.toMatchObject({
        status: 400,
        message: 'ERR_FILE_CONTENT_TOO_LONG',
        details: { char_count: 312000, limit: 300000, trial_chapters: 20 },
      })
    })

    it('throws default error message when error response is invalid', async () => {
      vi.mocked(global.fetch).mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('Invalid JSON')
        },
      } as Response)

      await expect(materialsApi.upload(mockFile)).rejects.toThrow('ERR_MATERIAL_UPLOAD_FAILED')
    })

    it('handles network errors', async () => {
      vi.mocked(global.fetch).mockRejectedValue(new Error('Network error'))

      await expect(materialsApi.upload(mockFile)).rejects.toThrow('Network error')
    })
  })

  describe('delete', () => {
    it('deletes material successfully', async () => {
      mockApiDelete.mockResolvedValue({ message: 'Deleted' })

      await materialsApi.delete('novel-1')

      expect(mockApiDelete).toHaveBeenCalledWith('/api/v1/materials/novel-1')
    })

    it('propagates API errors', async () => {
      mockApiDelete.mockRejectedValue(new Error('Not found'))

      await expect(materialsApi.delete('novel-1')).rejects.toThrow('Not found')
    })
  })

  describe('retry', () => {
    it('retries failed decomposition', async () => {
      const mockResponse = { message: 'Retry scheduled' }
      mockApiPost.mockResolvedValue(mockResponse)

      const result = await materialsApi.retry('novel-1')

      expect(result).toEqual(mockResponse)
      expect(mockApiPost).toHaveBeenCalledWith('/api/v1/materials/novel-1/retry')
    })

    it('propagates API errors', async () => {
      mockApiPost.mockRejectedValue(new Error('Retry failed'))

      await expect(materialsApi.retry('novel-1')).rejects.toThrow('Retry failed')
    })
  })

  describe('getTree', () => {
    it('returns material tree structure', async () => {
      const mockTree: MaterialTreeNode[] = [
        {
          id: 'chapter-1',
          type: 'chapter',
          title: 'Chapter 1',
          children: [
            {
              id: 'character-1',
              type: 'character',
              title: 'Hero',
            },
          ],
        },
      ]
      mockApiGet.mockResolvedValue({ tree: mockTree })

      const result = await materialsApi.getTree('novel-1')

      expect(result).toEqual({ tree: mockTree })
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/tree')
    })

    it('propagates API errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Not found'))

      await expect(materialsApi.getTree('novel-1')).rejects.toThrow('Not found')
    })
  })

  describe('getChapter', () => {
    it('returns chapter details', async () => {
      const mockChapter: MaterialChapter = {
        id: 'chapter-1',
        novel_id: 'novel-1',
        chapter_number: 1,
        title: 'Chapter 1',
        content: 'Chapter content',
        word_count: 100,
        summary: 'Chapter summary',
        created_at: '2024-01-01T00:00:00Z',
      }
      mockApiGet.mockResolvedValue(mockChapter)

      const result = await materialsApi.getChapter('novel-1', 'chapter-1')

      expect(result).toEqual(mockChapter)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/chapters/chapter-1')
    })

    it('propagates API errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Not found'))

      await expect(materialsApi.getChapter('novel-1', 'chapter-1')).rejects.toThrow('Not found')
    })
  })

  describe('getCharacters', () => {
    it('returns list of characters', async () => {
      const mockCharacters: MaterialCharacter[] = [
        {
          id: 'char-1',
          novel_id: 'novel-1',
          name: 'Hero',
          aliases: ['The Hero'],
          description: 'Main protagonist',
          first_appearance_chapter: 1,
          created_at: '2024-01-01T00:00:00Z',
        },
        {
          id: 'char-2',
          novel_id: 'novel-1',
          name: 'Villain',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockCharacters)

      const result = await materialsApi.getCharacters('novel-1')

      expect(result).toEqual(mockCharacters)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/characters')
    })

    it('returns empty array when no characters', async () => {
      mockApiGet.mockResolvedValue([])

      const result = await materialsApi.getCharacters('novel-1')

      expect(result).toEqual([])
    })
  })

  describe('getStories', () => {
    it('returns list of stories', async () => {
      const mockStories: MaterialStory[] = [
        {
          id: 'story-1',
          novel_id: 'novel-1',
          title: 'Main Story',
          synopsis: 'The main plot',
          chapter_range: '1-10',
          story_type: 'main',
          core_objective: 'Save the world',
          core_conflict: 'Good vs Evil',
          themes: 'Hope, sacrifice',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockStories)

      const result = await materialsApi.getStories('novel-1')

      expect(result).toEqual(mockStories)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/stories')
    })

    it('returns stories with minimal fields', async () => {
      const mockStories: MaterialStory[] = [
        {
          id: 'story-1',
          novel_id: 'novel-1',
          title: 'Side Story',
          synopsis: 'A side plot',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockStories)

      const result = await materialsApi.getStories('novel-1')

      expect(result).toEqual(mockStories)
    })
  })

  describe('getPlots', () => {
    it('returns list of plots', async () => {
      const mockPlots: MaterialPlot[] = [
        {
          id: 1,
          chapter_id: 1,
          index: 0,
          plot_type: 'climax',
          description: 'The final battle',
          characters: ['Hero', 'Villain'],
        },
      ]
      mockApiGet.mockResolvedValue(mockPlots)

      const result = await materialsApi.getPlots('novel-1')

      expect(result).toEqual(mockPlots)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/plots')
    })

    it('returns plots with null characters', async () => {
      const mockPlots: MaterialPlot[] = [
        {
          id: 1,
          chapter_id: 1,
          index: 0,
          plot_type: 'exposition',
          description: 'Background info',
          characters: null,
        },
      ]
      mockApiGet.mockResolvedValue(mockPlots)

      const result = await materialsApi.getPlots('novel-1')

      expect(result).toEqual(mockPlots)
    })
  })

  describe('getStoryLines', () => {
    it('returns list of story lines', async () => {
      const mockStoryLines: MaterialStoryLine[] = [
        {
          id: 1,
          novel_id: 1,
          title: 'Main Plot',
          description: 'The primary storyline',
          main_characters: ['Hero', 'Sidekick'],
          themes: ['Good vs Evil', 'Redemption'],
          stories_count: 10,
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockStoryLines)

      const result = await materialsApi.getStoryLines('novel-1')

      expect(result).toEqual(mockStoryLines)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/storylines')
    })

    it('returns story lines with null fields', async () => {
      const mockStoryLines: MaterialStoryLine[] = [
        {
          id: 1,
          novel_id: 1,
          title: 'Minor Plot',
          description: null,
          main_characters: null,
          themes: null,
          stories_count: 2,
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockStoryLines)

      const result = await materialsApi.getStoryLines('novel-1')

      expect(result).toEqual(mockStoryLines)
    })
  })

  describe('getRelationships', () => {
    it('returns list of character relationships', async () => {
      const mockRelationships: MaterialCharacterRelationship[] = [
        {
          id: 1,
          character_a_id: 1,
          character_a_name: 'Hero',
          character_b_id: 2,
          character_b_name: 'Villain',
          relationship_type: 'enemy',
          sentiment: 'negative',
          description: 'Arch enemies',
        },
      ]
      mockApiGet.mockResolvedValue(mockRelationships)

      const result = await materialsApi.getRelationships('novel-1')

      expect(result).toEqual(mockRelationships)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/relationships')
    })

    it('returns relationships with null sentiment and description', async () => {
      const mockRelationships: MaterialCharacterRelationship[] = [
        {
          id: 1,
          character_a_id: 1,
          character_a_name: 'Character A',
          character_b_id: 2,
          character_b_name: 'Character B',
          relationship_type: 'neutral',
          sentiment: null,
          description: null,
        },
      ]
      mockApiGet.mockResolvedValue(mockRelationships)

      const result = await materialsApi.getRelationships('novel-1')

      expect(result).toEqual(mockRelationships)
    })
  })

  describe('getGoldenFingers', () => {
    it('returns list of golden fingers', async () => {
      const mockGoldenFingers: MaterialGoldenFinger[] = [
        {
          id: 1,
          novel_id: 1,
          name: 'System',
          type: 'system',
          description: 'A game-like system',
          first_appearance_chapter_id: 1,
          evolution_history: [
            {
              stage: 'Level 1',
              chapter: 1,
              description: 'Initial awakening',
              timestamp: '2024-01-01T00:00:00Z',
            },
          ],
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockGoldenFingers)

      const result = await materialsApi.getGoldenFingers('novel-1')

      expect(result).toEqual(mockGoldenFingers)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/goldenfingers')
    })

    it('returns golden fingers with null fields', async () => {
      const mockGoldenFingers: MaterialGoldenFinger[] = [
        {
          id: 1,
          novel_id: 1,
          name: 'Cheat Item',
          type: 'item',
          description: null,
          first_appearance_chapter_id: null,
          evolution_history: null,
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockGoldenFingers)

      const result = await materialsApi.getGoldenFingers('novel-1')

      expect(result).toEqual(mockGoldenFingers)
    })
  })

  describe('getWorldView', () => {
    it('returns world view', async () => {
      const mockFactions: Faction[] = [
        {
          name: 'Empire',
          description: 'The ruling empire',
          leader: 'Emperor',
          territory: 'Central Plains',
        },
      ]
      const mockWorldView: MaterialWorldView = {
        id: 1,
        novel_id: 1,
        power_system: 'Cultivation',
        world_structure: 'Three realms',
        key_factions: mockFactions,
        special_rules: 'Karma system',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      }
      mockApiGet.mockResolvedValue(mockWorldView)

      const result = await materialsApi.getWorldView('novel-1')

      expect(result).toEqual(mockWorldView)
      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/novel-1/worldview')
    })

    it('returns null when no world view exists', async () => {
      mockApiGet.mockResolvedValue(null)

      const result = await materialsApi.getWorldView('novel-1')

      expect(result).toBeNull()
    })

    it('returns world view with null fields', async () => {
      const mockWorldView: MaterialWorldView = {
        id: 1,
        novel_id: 1,
        power_system: null,
        world_structure: null,
        key_factions: null,
        special_rules: null,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      }
      mockApiGet.mockResolvedValue(mockWorldView)

      const result = await materialsApi.getWorldView('novel-1')

      expect(result).toEqual(mockWorldView)
    })
  })

  it('no longer exposes the unused timeline endpoint', () => {
    expect('getTimeline' in materialsApi).toBe(false)
  })

  describe('Material Bridge APIs', () => {
    describe('getLibrarySummary', () => {
      it('returns library summary with entity counts', async () => {
        const mockSummary: LibrarySummaryItem[] = [
          {
            id: 1,
            title: 'Novel 1',
            status: 'completed',
            counts: {
              characters: 10,
              worldview: 1,
              golden_fingers: 3,
              storylines: 2,
              relationships: 15,
            },
          },
          {
            id: 2,
            title: 'Novel 2',
            status: 'processing',
            counts: {
              characters: 0,
              worldview: 0,
              golden_fingers: 0,
              storylines: 0,
              relationships: 0,
            },
          },
        ]
        mockApiGet.mockResolvedValue(mockSummary)

        const result = await materialsApi.getLibrarySummary()

        expect(result).toEqual(mockSummary)
        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/library-summary')
      })

      it('returns empty array when no libraries', async () => {
        mockApiGet.mockResolvedValue([])

        const result = await materialsApi.getLibrarySummary()

        expect(result).toEqual([])
      })
    })

    describe('searchMaterials', () => {
      it('returns search results', async () => {
        const mockResults: MaterialSearchResult[] = [
          {
            novel_id: 1,
            novel_title: 'Novel 1',
            entity_type: 'characters',
            entity_id: 1,
            name: 'Hero',
          },
          {
            novel_id: 1,
            novel_title: 'Novel 1',
            entity_type: 'worldview',
            entity_id: 1,
            name: 'Magic System',
          },
        ]
        mockApiGet.mockResolvedValue(mockResults)

        const result = await materialsApi.searchMaterials('hero')

        expect(result).toEqual(mockResults)
        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/search?q=hero')
      })

      it('properly encodes search query', async () => {
        mockApiGet.mockResolvedValue([])

        await materialsApi.searchMaterials('hero & villain')

        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/search?q=hero%20%26%20villain')
      })

      it('returns empty array when no results', async () => {
        mockApiGet.mockResolvedValue([])

        const result = await materialsApi.searchMaterials('nonexistent')

        expect(result).toEqual([])
      })
    })

    describe('getPreview', () => {
      it('returns preview for character entity', async () => {
        const mockPreview: MaterialPreviewResponse = {
          title: 'Hero',
          markdown: '# Hero\\n\\nMain protagonist',
          novel_title: 'Novel 1',
          suggested_file_type: 'character',
          suggested_folder_name: 'Characters',
          suggested_file_name: 'Hero',
        }
        mockApiGet.mockResolvedValue(mockPreview)

        const result = await materialsApi.getPreview(1, 'characters', 1)

        expect(result).toEqual(mockPreview)
        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/1/characters/1/preview')
      })

      it('returns preview for worldview entity', async () => {
        const mockPreview: MaterialPreviewResponse = {
          title: 'World View',
          markdown: '# World View\\n\\nMagic system',
          novel_title: 'Novel 1',
          suggested_file_type: 'lore',
          suggested_folder_name: 'Lore',
          suggested_file_name: 'World View',
        }
        mockApiGet.mockResolvedValue(mockPreview)

        const result = await materialsApi.getPreview(1, 'worldview', 1)

        expect(result).toEqual(mockPreview)
        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/1/worldview/1/preview')
      })

      it('returns preview for goldenfinger entity', async () => {
        const mockPreview: MaterialPreviewResponse = {
          title: 'System',
          markdown: '# System\\n\\nGame-like system',
          novel_title: 'Novel 1',
          suggested_file_type: 'lore',
          suggested_folder_name: 'Lore',
          suggested_file_name: 'System',
        }
        mockApiGet.mockResolvedValue(mockPreview)

        const result = await materialsApi.getPreview(1, 'goldenfingers', 1)

        expect(result).toEqual(mockPreview)
        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/1/goldenfingers/1/preview')
      })

      it('returns preview for storylines entity', async () => {
        const mockPreview: MaterialPreviewResponse = {
          title: 'Main Plot',
          markdown: '# Main Plot\\n\\nPrimary storyline',
          novel_title: 'Novel 1',
          suggested_file_type: 'outline',
          suggested_folder_name: 'Outlines',
          suggested_file_name: 'Main Plot',
        }
        mockApiGet.mockResolvedValue(mockPreview)

        const result = await materialsApi.getPreview(1, 'storylines', 1)

        expect(result).toEqual(mockPreview)
        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/1/storylines/1/preview')
      })

      it('returns preview for relationships entity', async () => {
        const mockPreview: MaterialPreviewResponse = {
          title: 'Hero - Villain Relationship',
          markdown: '# Relationship\\n\\nArch enemies',
          novel_title: 'Novel 1',
          suggested_file_type: 'lore',
          suggested_folder_name: 'Lore',
          suggested_file_name: 'Hero - Villain',
        }
        mockApiGet.mockResolvedValue(mockPreview)

        const result = await materialsApi.getPreview(1, 'relationships', 1)

        expect(result).toEqual(mockPreview)
        expect(mockApiGet).toHaveBeenCalledWith('/api/v1/materials/1/relationships/1/preview')
      })
    })

    describe('importToProject', () => {
      it('imports material to project', async () => {
        const mockRequest: MaterialImportRequest = {
          project_id: 'project-1',
          novel_id: 1,
          entity_type: 'characters',
          entity_id: 1,
          file_name: 'Hero',
          target_folder_id: 'folder-1',
        }
        const mockResponse: MaterialImportResponse = {
          file_id: 'file-1',
          title: 'Hero',
          folder_name: 'Characters',
          file_type: 'character',
        }
        mockApiPost.mockResolvedValue(mockResponse)

        const result = await materialsApi.importToProject(mockRequest)

        expect(result).toEqual(mockResponse)
        expect(mockApiPost).toHaveBeenCalledWith('/api/v1/materials/import', mockRequest)
      })

      it('imports without optional fields', async () => {
        const mockRequest: MaterialImportRequest = {
          project_id: 'project-1',
          novel_id: 1,
          entity_type: 'worldview',
          entity_id: 1,
        }
        const mockResponse: MaterialImportResponse = {
          file_id: 'file-1',
          title: 'World View',
          folder_name: 'Lore',
          file_type: 'lore',
        }
        mockApiPost.mockResolvedValue(mockResponse)

        const result = await materialsApi.importToProject(mockRequest)

        expect(result).toEqual(mockResponse)
        expect(mockApiPost).toHaveBeenCalledWith('/api/v1/materials/import', mockRequest)
      })

      it('propagates API errors', async () => {
        const mockRequest: MaterialImportRequest = {
          project_id: 'project-1',
          novel_id: 1,
          entity_type: 'characters',
          entity_id: 1,
        }
        mockApiPost.mockRejectedValue(new Error('Import failed'))

        await expect(materialsApi.importToProject(mockRequest)).rejects.toThrow('Import failed')
      })
    })

    describe('batchImport', () => {
      it('imports multiple materials to project', async () => {
        const mockItems: BatchImportItem[] = [
          { novel_id: 1, entity_type: 'characters', entity_id: 1 },
          { novel_id: 1, entity_type: 'characters', entity_id: 2 },
          { novel_id: 1, entity_type: 'worldview', entity_id: 1 },
        ]
        const mockResponse: BatchImportResponse = {
          results: [
            {
              file_id: 'file-1',
              title: 'Hero',
              folder_name: 'Characters',
              file_type: 'character',
            },
            {
              file_id: 'file-2',
              title: 'Villain',
              folder_name: 'Characters',
              file_type: 'character',
            },
            {
              file_id: 'file-3',
              title: 'World View',
              folder_name: 'Lore',
              file_type: 'lore',
            },
          ],
          failed_count: 0,
        }
        mockApiPost.mockResolvedValue(mockResponse)

        const result = await materialsApi.batchImport('project-1', mockItems)

        expect(result).toEqual(mockResponse)
        expect(mockApiPost).toHaveBeenCalledWith('/api/v1/materials/batch-import', {
          project_id: 'project-1',
          items: mockItems,
        })
      })

      it('handles partial failures in batch import', async () => {
        const mockItems: BatchImportItem[] = [
          { novel_id: 1, entity_type: 'characters', entity_id: 1 },
          { novel_id: 1, entity_type: 'characters', entity_id: 999 },
        ]
        const mockResponse: BatchImportResponse = {
          results: [
            {
              file_id: 'file-1',
              title: 'Hero',
              folder_name: 'Characters',
              file_type: 'character',
            },
          ],
          failed_count: 1,
        }
        mockApiPost.mockResolvedValue(mockResponse)

        const result = await materialsApi.batchImport('project-1', mockItems)

        expect(result.failed_count).toBe(1)
        expect(result.results).toHaveLength(1)
      })

      it('handles all items failing', async () => {
        const mockItems: BatchImportItem[] = [
          { novel_id: 1, entity_type: 'characters', entity_id: 999 },
          { novel_id: 1, entity_type: 'characters', entity_id: 998 },
        ]
        const mockResponse: BatchImportResponse = {
          results: [],
          failed_count: 2,
        }
        mockApiPost.mockResolvedValue(mockResponse)

        const result = await materialsApi.batchImport('project-1', mockItems)

        expect(result.failed_count).toBe(2)
        expect(result.results).toHaveLength(0)
      })

      it('propagates API errors', async () => {
        const mockItems: BatchImportItem[] = [
          { novel_id: 1, entity_type: 'characters', entity_id: 1 },
        ]
        mockApiPost.mockRejectedValue(new Error('Batch import failed'))

        await expect(materialsApi.batchImport('project-1', mockItems)).rejects.toThrow(
          'Batch import failed'
        )
      })
    })
  })

  describe('integration scenarios', () => {
    it('full workflow: upload and poll material details', async () => {
      // Upload
      const mockFile = new File(['content'], 'test.txt', { type: 'text/plain' })
      const mockUploadResponse: MaterialUploadResponse = {
        novel_id: 'novel-1',
        message: 'Upload successful',
        status: 'pending',
      }
      vi.mocked(global.fetch).mockResolvedValue({
        ok: true,
        json: async () => mockUploadResponse,
      } as Response)

      const uploadResult = await materialsApi.upload(mockFile)
      expect(uploadResult.novel_id).toBe('novel-1')

      // Get details
      const mockNovel: MaterialNovel = {
        id: 'novel-1',
        user_id: 'user-1',
        title: 'Test Novel',
        original_filename: 'test.txt',
        file_size: 1024,
        status: 'completed',
        total_chapters: 10,
        chapters_count: 10,
        total_characters: 5,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      }
      mockApiGet.mockResolvedValue(mockNovel)

      const novel = await materialsApi.get('novel-1')
      expect(novel.status).toBe('completed')
    })

    it('search and import workflow', async () => {
      // Search
      const mockResults: MaterialSearchResult[] = [
        {
          novel_id: 1,
          novel_title: 'Novel 1',
          entity_type: 'characters',
          entity_id: 1,
          name: 'Hero',
        },
      ]
      mockApiGet.mockResolvedValue(mockResults)

      const searchResults = await materialsApi.searchMaterials('hero')
      expect(searchResults).toHaveLength(1)

      // Get preview
      const mockPreview: MaterialPreviewResponse = {
        title: 'Hero',
        markdown: '# Hero\\n\\nMain protagonist',
        novel_title: 'Novel 1',
        suggested_file_type: 'character',
        suggested_folder_name: 'Characters',
        suggested_file_name: 'Hero',
      }
      mockApiGet.mockResolvedValue(mockPreview)

      const preview = await materialsApi.getPreview(1, 'characters', 1)
      expect(preview.title).toBe('Hero')

      // Import to project
      const mockRequest: MaterialImportRequest = {
        project_id: 'project-1',
        novel_id: 1,
        entity_type: 'characters',
        entity_id: 1,
      }
      const mockResponse: MaterialImportResponse = {
        file_id: 'file-1',
        title: 'Hero',
        folder_name: 'Characters',
        file_type: 'character',
      }
      mockApiPost.mockResolvedValue(mockResponse)

      const importResult = await materialsApi.importToProject(mockRequest)
      expect(importResult.file_id).toBe('file-1')
    })

    it('batch import workflow', async () => {
      // Get library summary
      const mockSummary: LibrarySummaryItem[] = [
        {
          id: 1,
          title: 'Novel 1',
          status: 'completed',
          counts: {
            characters: 10,
            worldview: 1,
            golden_fingers: 3,
            storylines: 2,
            relationships: 15,
          },
        },
      ]
      mockApiGet.mockResolvedValue(mockSummary)

      const summary = await materialsApi.getLibrarySummary()
      expect(summary[0].counts.characters).toBe(10)

      // Batch import characters
      const mockItems: BatchImportItem[] = [
        { novel_id: 1, entity_type: 'characters', entity_id: 1 },
        { novel_id: 1, entity_type: 'characters', entity_id: 2 },
        { novel_id: 1, entity_type: 'characters', entity_id: 3 },
      ]
      const mockBatchResponse: BatchImportResponse = {
        results: [
          {
            file_id: 'file-1',
            title: 'Character 1',
            folder_name: 'Characters',
            file_type: 'character',
          },
          {
            file_id: 'file-2',
            title: 'Character 2',
            folder_name: 'Characters',
            file_type: 'character',
          },
          {
            file_id: 'file-3',
            title: 'Character 3',
            folder_name: 'Characters',
            file_type: 'character',
          },
        ],
        failed_count: 0,
      }
      mockApiPost.mockResolvedValue(mockBatchResponse)

      const batchResult = await materialsApi.batchImport('project-1', mockItems)
      expect(batchResult.results).toHaveLength(3)
      expect(batchResult.failed_count).toBe(0)
    })

    it('concurrent requests handling', async () => {
      const mockNovels: MaterialNovel[] = [
        {
          id: 'novel-1',
          user_id: 'user-1',
          title: 'Novel 1',
          original_filename: 'test1.txt',
          file_size: 1024,
          status: 'completed',
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        },
      ]
      const mockCharacters: MaterialCharacter[] = [
        {
          id: 'char-1',
          novel_id: 'novel-1',
          name: 'Hero',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      const mockStories: MaterialStory[] = [
        {
          id: 'story-1',
          novel_id: 'novel-1',
          title: 'Main Story',
          synopsis: 'Plot',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]

      mockApiGet
        .mockResolvedValueOnce(mockNovels)
        .mockResolvedValueOnce(mockCharacters)
        .mockResolvedValueOnce(mockStories)

      const [novels, characters, stories] = await Promise.all([
        materialsApi.list(),
        materialsApi.getCharacters('novel-1'),
        materialsApi.getStories('novel-1'),
      ])

      expect(novels).toEqual(mockNovels)
      expect(characters).toEqual(mockCharacters)
      expect(stories).toEqual(mockStories)
      expect(mockApiGet).toHaveBeenCalledTimes(3)
    })
  })

  describe('error handling', () => {
    it('handles 404 not found errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Not found'))

      await expect(materialsApi.get('nonexistent')).rejects.toThrow('Not found')
    })

    it('handles 401 unauthorized errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Unauthorized'))

      await expect(materialsApi.list()).rejects.toThrow('Unauthorized')
    })

    it('handles 500 server errors', async () => {
      mockApiDelete.mockRejectedValue(new Error('Internal Server Error'))

      await expect(materialsApi.delete('novel-1')).rejects.toThrow('Internal Server Error')
    })

    it('handles network timeout errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Request timeout'))

      await expect(materialsApi.getTree('novel-1')).rejects.toThrow('Request timeout')
    })

    it('handles connection refused errors', async () => {
      mockApiPost.mockRejectedValue(new Error('Connection refused'))

      await expect(materialsApi.retry('novel-1')).rejects.toThrow('Connection refused')
    })
  })

  describe('type safety', () => {
    it('returns correctly typed MaterialNovel objects', async () => {
      const mockNovel: MaterialNovel = {
        id: 'novel-1',
        user_id: 'user-1',
        title: 'Test Novel',
        original_filename: 'test.txt',
        file_size: 1024,
        status: 'completed',
        total_chapters: 10,
        chapters_count: 10,
        total_characters: 5,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      }
      mockApiGet.mockResolvedValue(mockNovel)

      const result = await materialsApi.get('novel-1')

      expect(typeof result.id).toBe('string')
      expect(typeof result.user_id).toBe('string')
      expect(typeof result.title).toBe('string')
      expect(typeof result.original_filename).toBe('string')
      expect(typeof result.file_size).toBe('number')
      expect(['pending', 'processing', 'completed', 'failed']).toContain(result.status)
      expect(typeof result.created_at).toBe('string')
      expect(typeof result.updated_at).toBe('string')
    })

    it('returns correctly typed MaterialCharacter objects', async () => {
      const mockCharacters: MaterialCharacter[] = [
        {
          id: 'char-1',
          novel_id: 'novel-1',
          name: 'Hero',
          aliases: ['The Hero', 'Protagonist'],
          description: 'Main character',
          first_appearance_chapter: 1,
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockCharacters)

      const result = await materialsApi.getCharacters('novel-1')

      expect(typeof result[0].id).toBe('string')
      expect(typeof result[0].novel_id).toBe('string')
      expect(typeof result[0].name).toBe('string')
      expect(Array.isArray(result[0].aliases)).toBe(true)
      expect(typeof result[0].description).toBe('string')
      expect(typeof result[0].first_appearance_chapter).toBe('number')
      expect(typeof result[0].created_at).toBe('string')
    })

    it('returns correctly typed MaterialSearchResult objects', async () => {
      const mockResults: MaterialSearchResult[] = [
        {
          novel_id: 1,
          novel_title: 'Novel 1',
          entity_type: 'characters',
          entity_id: 1,
          name: 'Hero',
        },
      ]
      mockApiGet.mockResolvedValue(mockResults)

      const result = await materialsApi.searchMaterials('hero')

      expect(typeof result[0].novel_id).toBe('number')
      expect(typeof result[0].novel_title).toBe('string')
      expect(['characters', 'worldview', 'goldenfingers', 'storylines', 'relationships']).toContain(
        result[0].entity_type
      )
      expect(typeof result[0].entity_id).toBe('number')
      expect(typeof result[0].name).toBe('string')
    })

    it('returns correctly typed LibrarySummaryItem objects', async () => {
      const mockSummary: LibrarySummaryItem[] = [
        {
          id: 1,
          title: 'Novel 1',
          status: 'completed',
          counts: {
            characters: 10,
            worldview: 1,
            golden_fingers: 3,
            storylines: 2,
            relationships: 15,
          },
        },
      ]
      mockApiGet.mockResolvedValue(mockSummary)

      const result = await materialsApi.getLibrarySummary()

      expect(typeof result[0].id).toBe('number')
      expect(typeof result[0].title).toBe('string')
      expect(typeof result[0].counts.characters).toBe('number')
      expect(typeof result[0].counts.worldview).toBe('number')
      expect(typeof result[0].counts.golden_fingers).toBe('number')
      expect(typeof result[0].counts.storylines).toBe('number')
      expect(typeof result[0].counts.relationships).toBe('number')
    })
  })

  describe('MaterialEntityType validation', () => {
    it('accepts valid entity types', async () => {
      const validTypes: MaterialEntityType[] = [
        'characters',
        'worldview',
        'goldenfingers',
        'storylines',
        'relationships',
      ]

      for (const type of validTypes) {
        const mockPreview: MaterialPreviewResponse = {
          title: 'Test',
          markdown: 'Content',
          novel_title: 'Novel',
          suggested_file_type: 'lore',
          suggested_folder_name: 'Folder',
          suggested_file_name: 'Test',
        }
        mockApiGet.mockResolvedValue(mockPreview)

        const result = await materialsApi.getPreview(1, type, 1)
        expect(result).toBeDefined()
      }
    })
  })
})
