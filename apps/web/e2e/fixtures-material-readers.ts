import type { Page } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';
import { referenceMaterial, referenceCharacter, referenceStory, referencePlot, referenceStoryline, referenceRelationship, referenceGoldenFinger, referenceWorldview } from '../src/test/materialReaderFixtures';
export { referenceCases } from '../src/test/materialReaderFixtures';

export async function mockMaterialReaders(page: Page) {
  await mockResponsiveApp(page, null, 'pro');
  const responses: Record<string, unknown> = {
    '': referenceMaterial, '/characters': [referenceCharacter], '/stories': [referenceStory],
    '/plots': [referencePlot], '/storylines': [referenceStoryline], '/relationships': [referenceRelationship],
    '/goldenfingers': [referenceGoldenFinger], '/worldview': referenceWorldview,
  };
  await page.route('**/api/v1/materials/24{,/**}', async route => {
    const suffix = new URL(route.request().url()).pathname.replace('/api/v1/materials/24', '');
    if (Object.hasOwn(responses, suffix)) await route.fulfill({ json: responses[suffix] });
    else await route.fallback();
  });
}
