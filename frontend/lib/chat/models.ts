export type Model = {
  id: string;
  label: string;
  description: string;
};

export const MODELS: Model[] = [
  {
    id: "claude-opus-5-5",
    label: "Claude Opus 5.5",
    description: "Most capable, for complex work",
  },
  {
    id: "claude-sonnet-5-5",
    label: "Claude Sonnet 5.5",
    description: "Balanced speed and intelligence",
  },
  {
    id: "claude-haiku-4-5-20251001",
    label: "Claude Haiku 4.5",
    description: "Fastest, for everyday tasks",
  },
];

export const DEFAULT_MODEL_ID = MODELS[1].id;

export function getModel(id: string | undefined): Model {
  return MODELS.find((model) => model.id === id) ?? MODELS[1];
}
