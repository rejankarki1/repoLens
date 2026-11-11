/** What each demo (guest-showcase) repository is, written from its own README. The API carries no
 * description field, so this is the one place that names what the two demo repositories actually do;
 * used by both the landing page and the guest dashboard so the two stay in sync. */

export interface DemoDescription {
  summary: string
  stack: string
}

const DEMO_DESCRIPTIONS: Record<string, DemoDescription> = {
  'rejankarki1/classlens': {
    summary:
      'A mobile notebook for college students. It turns photos of lecture slides and handwritten notes into organized course material, with grounded Q&A and quizzes.',
    stack: 'Expo · React Native · Supabase · Gemini',
  },
  'rejankarki1/wanderlust': {
    summary: 'A full-stack travel-listing app: browse and publish stays, reserve trips, write reviews, and see each listing on a map.',
    stack: 'React · Express · MongoDB · Mapbox',
  },
}

export function demoDescription(fullName: string): DemoDescription | undefined {
  return DEMO_DESCRIPTIONS[fullName.toLowerCase()]
}
