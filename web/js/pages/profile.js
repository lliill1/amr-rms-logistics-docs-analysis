import { button, pill } from '../components.js';
import { h } from '../dom.js';
import { icon } from '../icons.js';

function initials(value) {
  const text = String(value || 'Пользователь').trim();
  return text.slice(0, 2).toLocaleUpperCase('ru-RU');
}

export function createProfilePage({ user = 'Пользователь', onSwitchAccount } = {}) {
  const accountName = String(user || 'Пользователь');

  const el = h('div', { class: 'stack profile-page', style: { gap: '22px' } },
    h('div', { class: 'page-head' },
      h('div', null,
        h('div', { class: 'eyebrow' }, 'АККАУНТ'),
        h('h1', { class: 'page-title' }, 'Личный кабинет'),
        h('p', { class: 'page-sub' }, 'Профиль, активность и настройки вашего рабочего пространства.'))),

    h('div', { class: 'profile-layout' },
      h('section', { class: 'card profile-card' },
        h('div', { class: 'profile-card__top' },
          h('div', { class: 'profile-avatar' }, initials(accountName)),
          h('div', { class: 'profile-card__identity' },
            h('div', { class: 'profile-card__name' }, accountName),
            h('div', { class: 'profile-card__login' }, `Логин: ${accountName}`),
            pill('Демо-профиль', 'info', { dot: true, sm: true }))),
        h('div', { class: 'profile-separator' }),
        h('div', { class: 'profile-details' },
          h('div', { class: 'profile-detail' }, h('span', null, 'Тип аккаунта'), h('strong', null, 'Пользователь')),
          h('div', { class: 'profile-detail' }, h('span', null, 'Статус'), h('strong', { class: 'profile-status' }, icon('circle-check', 15), 'Активен в демо-режиме')),
          h('div', { class: 'profile-detail' }, h('span', null, 'Хранение данных'), h('strong', null, 'Локально'))),
        h('div', { class: 'profile-note' }, icon('info', 16),
          h('span', null, 'Это демонстрационный экран. Данные профиля пока не связаны с сервером и не сохраняются в аккаунте.'))),

      h('div', { class: 'profile-side stack' },
        h('section', { class: 'card profile-summary' },
          h('div', { class: 'card__head' }, h('div', { class: 'card__icon card__icon--accent' }, icon('activity', 17)),
            h('div', null, h('div', { class: 'card__title' }, 'Активность'), h('div', { class: 'card__sub' }, 'Демонстрационная сводка'))),
          h('div', { class: 'profile-stats' },
            h('div', null, h('strong', null, '04'), h('span', null, 'события')),
            h('div', null, h('strong', null, '02'), h('span', null, 'анализа')),
            h('div', null, h('strong', null, '01'), h('span', null, 'отчёт')))),
        h('section', { class: 'card profile-shortcuts' },
          h('div', { class: 'card__title' }, 'Быстрый доступ'),
          h('a', { href: '#/archive', class: 'profile-shortcut' }, icon('folder-archive', 17), h('span', null, 'Архив и обработка'), icon('chevron-right', 16)),
          h('a', { href: '#/report', class: 'profile-shortcut' }, icon('file-text', 17), h('span', null, 'Отчёты'), icon('chevron-right', 16))))),

    h('a', { class: 'card profile-journal', href: '#/history', 'aria-label': 'Открыть журнал действий' },
      h('div', { class: 'profile-journal__icon' }, icon('history', 22)),
      h('div', { class: 'profile-journal__content' },
        h('div', { class: 'profile-journal__eyebrow' }, 'ИСТОРИЯ РАБОТЫ'),
        h('div', { class: 'profile-journal__title' }, 'Журнал действий'),
        h('p', null, 'Посмотрите, какие документы загружались, какие анализы выполнялись и какие отчёты создавались.')),
      h('div', { class: 'profile-journal__meta' },
        pill('04 события', 'info', { dot: false, sm: true }),
        icon('arrow-up-right', 19))),

    h('section', { class: 'card profile-settings' },
      h('div', { class: 'profile-settings__copy' },
        h('div', { class: 'card__title' }, 'Настройки аккаунта'),
        h('p', { class: 'muted' }, 'В будущем здесь можно будет редактировать имя, контактные данные, пароль и параметры уведомлений.')),
      pill('Скоро', 'neutral', { dot: false, sm: true })),

    h('section', { class: 'card profile-account-switch' },
      h('div', { class: 'profile-account-switch__copy' },
        h('div', { class: 'profile-account-switch__label' }, 'УПРАВЛЕНИЕ АККАУНТОМ'),
        h('div', { class: 'card__title' }, 'Сменить аккаунт'),
        h('p', { class: 'muted' }, 'Завершить текущий демонстрационный вход и перейти к форме авторизации под другим логином.')),
      button({ label: 'Сменить аккаунт', icon: 'arrow-right-left', variant: 'secondary', onClick: onSwitchAccount })));

  return { el, destroy() {} };
}
