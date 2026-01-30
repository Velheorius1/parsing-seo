import Bottleneck from 'bottleneck';

// Yandex Suggest — максимум 1 запрос в секунду
export const yandexLimiter = new Bottleneck({
  minTime: 1000,
  maxConcurrent: 1,
});

// Apify — 1 запрос каждые 2 секунды
export const apifyLimiter = new Bottleneck({
  minTime: 2000,
  maxConcurrent: 2,
});
