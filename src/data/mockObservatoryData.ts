import {
  ObservatoryDashboard,
  ObservatoryEntryDetail,
  ObservatoryEntryListItem,
  ObservatoryListResponse,
  ObservatorySourceDetail,
  ObservatoryTopicNode,
  EntriesQueryParams,
} from '../types/observatory';

export const MOCK_SOURCES: ObservatorySourceDetail[] = [
  {
    id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
    name: 'Competition Appeal Tribunal - Judgments',
    type: 'website',
    url: 'https://www.catribunal.org.uk/judgments',
    entry_count: 20,
    latest_published_at: '2026-09-03T14:30:00Z',
  },
  {
    id: '01b2a969-9524-4f05-b045-8f6fc814f85e',
    name: 'CNMC - Sala de Competencia',
    type: 'website',
    url: 'https://www.cnmc.es/prensa',
    entry_count: 20,
    latest_published_at: '2026-09-02T10:15:00Z',
  },
  {
    id: 'e6f47df4-3d9a-412e-9d2a-89a03fc5b931',
    name: 'Court of Justice of the European Union (CJEU)',
    type: 'website',
    url: 'https://curia.europa.eu',
    entry_count: 20,
    latest_published_at: '2026-09-03T09:00:00Z',
  },
  {
    id: '7a12b345-c89d-4e5f-b012-3456789abcde',
    name: 'European Commission - DG Competition',
    type: 'rss',
    url: 'https://ec.europa.eu/competition/antitrust/news',
    entry_count: 20,
    latest_published_at: '2026-08-30T16:45:00Z',
  },
];

export const MOCK_TOPICS: ObservatoryTopicNode[] = [
  {
    code: 'private_enforcement',
    name: 'Aplicación privada',
    parent_code: null,
    description: 'Litigación ante tribunales civiles ordinarios y especializados',
    children: [
      {
        code: 'damages_actions',
        name: 'Acciones de daños y perjuicios',
        parent_code: 'private_enforcement',
        description: 'Reclamaciones indemnizatorias derivadas de infracciones anticompetitivas',
        children: [],
      },
      {
        code: 'collective_actions',
        name: 'Acciones colectivas y representativas',
        parent_code: 'private_enforcement',
        description: 'Mecanismos opt-in y opt-out en litigación masiva',
        children: [],
      },
    ],
  },
  {
    code: 'anticompetitive_agreements',
    name: 'Acuerdos colusorios y cárteles',
    parent_code: null,
    description: 'Infracciones del artículo 101 TFUE y artículo 1 LDC',
    children: [
      {
        code: 'cartels',
        name: 'Cárteles horizontales',
        parent_code: 'anticompetitive_agreements',
        description: 'Fijación de precios, reparto de mercados y licitaciones colusorias',
        children: [],
      },
      {
        code: 'vertical_restraints',
        name: 'Restricciones verticales',
        parent_code: 'anticompetitive_agreements',
        description: 'Precios de reventa recomendados, distribución exclusiva y comercio electrónico',
        children: [],
      },
    ],
  },
  {
    code: 'abuse_dominance',
    name: 'Abuso de posición de dominio',
    parent_code: null,
    description: 'Artículo 102 TFUE y artículo 2 LDC',
    children: [
      {
        code: 'essential_facilities',
        name: 'Instalaciones esenciales y denegación de suministro',
        parent_code: 'abuse_dominance',
        description: 'Acceso a redes, APIs e infraestructuras críticas',
        children: [],
      },
      {
        code: 'excessive_pricing',
        name: 'Precios excesivos o predatorios',
        parent_code: 'abuse_dominance',
        description: 'Explotación económica y exclusión mediante precios por debajo de coste',
        children: [],
      },
    ],
  },
  {
    code: 'merger_control',
    name: 'Control de concentraciones',
    parent_code: null,
    description: 'Operaciones de M&A y control previo de adquisiciones',
    children: [
      {
        code: 'phase_2_remedies',
        name: 'Compromisos y desinversiones (Fase II)',
        parent_code: 'merger_control',
        description: 'Remedios estructurales y de comportamiento',
        children: [],
      },
      {
        code: 'gun_jumping',
        name: 'Ejecución prematura (Gun jumping)',
        parent_code: 'merger_control',
        description: 'Sanciones por cierre transaccional sin autorización previa',
        children: [],
      },
    ],
  },
  {
    code: 'digital_markets',
    name: 'Mercados Digitales y Plataformas',
    parent_code: null,
    description: 'Regulación ex-ante (DMA) y litigación sobre ecosistemas digitales',
    children: [
      {
        code: 'gatekeeper_regulations',
        name: 'Obligaciones de guardianes de acceso (DMA)',
        parent_code: 'digital_markets',
        description: 'Interoperabilidad, neutralidad y portabilidad de datos',
        children: [],
      },
      {
        code: 'self_preferencing',
        name: 'Autopreferencia algorítmica',
        parent_code: 'digital_markets',
        description: 'Privilegio de servicios propios en motores de búsqueda y marketplaces',
        children: [],
      },
    ],
  },
];

export const MOCK_ENTRIES: ObservatoryEntryDetail[] = [
  {
    entry_id: 'ada5d125-a861-4ff8-bcf6-2b2607afd834',
    title: '[2026] EWCA Civ 993 | Dr Liza Lovdahl Gormsen v Meta Platforms, Inc.',
    source: {
      id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
      name: 'Competition Appeal Tribunal - Judgments',
    },
    published_at: '2026-09-03T14:30:00Z',
    url: 'https://www.catribunal.org.uk/judgments/1434-5-7-22-t-dr-liza-lovdahl-gormsen-v-meta-platforms-inc',
    content_type: 'judgment',
    relevance: {
      status: 'relevant',
      score: 96,
      confidence: 1.0,
    },
    summary: 'La Court of Appeal desestima el recurso de apelación de Meta Platforms en el marco de la acción colectiva opt-out sobre condiciones abusivas de explotación de datos de usuarios de Facebook. Se valida la metodología de daño económico basada en precios injustos de acceso a datos personales.',
    canonical_topics: [
      { code: 'collective_actions', name: 'Acciones colectivas y representativas' },
      { code: 'abuse_dominance', name: 'Abuso de posición de dominio' },
    ],
    canonical_primary_topic: { code: 'collective_actions', name: 'Acciones colectivas y representativas' },
    key_points: [
      'Confirmación de la admisibilidad del procedimiento de reclamación masiva colectiva bajo la sección 47B de la Competition Act 1998.',
      'Validez del enfoque contrafactual respecto al valor monetario de los datos aportados por los usuarios finales.',
      'Rechazo a la pretensión de Meta de exigir prueba individualizada de daños en fase de certificación colectiva.'
    ],
    evidence: {
      source: 'deep',
      summary_quotes: [
        {
          source_field: 'content',
          quote: 'The proposed class representative has demonstrated a blueprint for trial that satisfies the requirements of Pro-Sys and Merricks.'
        },
        {
          source_field: 'content',
          quote: 'Meta\'s submission that user data has zero economic counterfactual value cannot be sustained at this interlocutory stage.'
        }
      ],
      key_points: [
        {
          point: 'Confirmación de la admisibilidad del procedimiento de reclamación masiva colectiva bajo la sección 47B de la Competition Act 1998.',
          quotes: [
            {
              source_field: 'content',
              quote: 'We conclude that the Tribunal applied the correct legal standard under Section 47B of the Competition Act 1998.'
            }
          ]
        },
        {
          point: 'Validez del enfoque contrafactual respecto al valor monetario de los datos aportados por los usuarios finales.',
          quotes: [
            {
              source_field: 'content',
              quote: 'The economic methodology presented by Dr Gormsen establishes a plausible nexus between the unfair trading conditions and the aggregate loss.'
            }
          ]
        },
        {
          point: 'Rechazo a la pretensión de Meta de exigir prueba individualizada de daños en fase de certificación colectiva.',
          quotes: [
            {
              source_field: 'content',
              quote: 'Individualised assessment of loss is precisely what the collective proceedings regime was designed to displace in appropriate consumer class claims.'
            }
          ]
        }
      ]
    }
  },
  {
    entry_id: '27c1a107-ebfe-40d0-ba9e-d7d399c3c565',
    title: 'Asunto C-60/25 [Livronsa] | Sentencia del Tribunal de Justicia (Gran Sala)',
    source: {
      id: 'e6f47df4-3d9a-412e-9d2a-89a03fc5b931',
      name: 'Court of Justice of the European Union (CJEU)',
    },
    published_at: '2026-09-03T09:00:00Z',
    url: 'https://curia.europa.eu/juris/document/document.jsf?text=&docid=289000',
    content_type: 'judgment',
    relevance: {
      status: 'relevant',
      score: 95,
      confidence: 1.0,
    },
    summary: 'Sentencia capital sobre abuso de posición de dominio en mercados farmacéuticos. El TJUE declara que la negativa injustificada de un laboratorio dominante a suministrar fármacos innovadores a mayoristas autorizados constituye infracción del artículo 102 TFUE si distorsiona el comercio transfronterizo legítimo.',
    canonical_topics: [
      { code: 'essential_facilities', name: 'Instalaciones esenciales y denegación de suministro' },
      { code: 'abuse_dominance', name: 'Abuso de posición de dominio' },
    ],
    canonical_primary_topic: { code: 'essential_facilities', name: 'Instalaciones esenciales y denegación de suministro' },
    key_points: [
      'Calificación de la denegación de suministro de medicamentos como abuso autónomo cuando concurren cuotas de cuasimonopolio.',
      'Incompatibilidad con el Derecho de la Unión de sistemas de doble precio diseñados para impedir el comercio paralelo comunitario.',
      'Fijación del estándar de causalidad exigible a las autoridades nacionales de competencia para imponer medidas cautelares.'
    ],
    evidence: {
      source: 'deep',
      summary_quotes: [
        {
          source_field: 'content',
          quote: 'Article 102 TFEU must be interpreted as precluding an undertaking in a dominant position from refusing to deliver regular orders placed by existing wholesalers.'
        }
      ],
      key_points: [
        {
          point: 'Calificación de la denegación de suministro de medicamentos como abuso autónomo cuando concurren cuotas de cuasimonopolio.',
          quotes: [
            {
              source_field: 'content',
              quote: 'A refusal to supply which aims at eliminating parallel trade constitutes an abuse of a dominant position within the meaning of Article 102.'
            }
          ]
        },
        {
          point: 'Incompatibilidad con el Derecho de la Unión de sistemas de doble precio diseñados para impedir el comercio paralelo comunitario.',
          quotes: [
            {
              source_field: 'content',
              quote: 'Differential pricing structures intended to disincentivise cross-border distribution infringe the fundamental objectives of the internal market.'
            }
          ]
        }
      ]
    }
  },
  {
    entry_id: 'b140263f-9173-4554-94e4-b7c10b05bfe1',
    title: 'Resolución S/0014/24: Cártel de licitaciones de infraestructuras ferroviarias',
    source: {
      id: '01b2a969-9524-4f05-b045-8f6fc814f85e',
      name: 'CNMC - Sala de Competencia',
    },
    published_at: '2026-09-02T10:15:00Z',
    url: 'https://www.cnmc.es/expedientes/s-0014-24',
    content_type: 'decision',
    relevance: {
      status: 'relevant',
      score: 92,
      confidence: 1.0,
    },
    summary: 'La CNMC sanciona con 47 millones de euros a 6 empresas constructoras por coordinar ofertas y repartirse licitaciones públicas de electrificación ferroviaria durante 8 años. Se impone la prohibición de contratar con la administración pública a cuatro directivos involucrados.',
    canonical_topics: [
      { code: 'cartels', name: 'Cárteles horizontales' },
      { code: 'damages_actions', name: 'Acciones de daños y perjuicios' },
    ],
    canonical_primary_topic: { code: 'cartels', name: 'Cárteles horizontales' },
    key_points: [
      'Acreditación de reuniones secretas periódicas e intercambio sistemático de hojas de cálculo de reparto de lotes.',
      'Aplicación del programa de clemencia: exención del 100% de la multa al solicitante que aportó pruebas concluyentes.',
      'Activación de la cláusula de prohibición de contratar con el sector público conforme a la Ley de Contratos del Sector Público.'
    ],
    evidence: {
      source: 'deep',
      summary_quotes: [
        {
          source_field: 'content',
          quote: 'Se declara la existencia de una infracción muy grave y continuada del artículo 1 de la Ley 15/2007 de Defensa de la Competencia.'
        }
      ],
      key_points: [
        {
          point: 'Acreditación de reuniones secretas periódicas e intercambio sistemático de hojas de cálculo de reparto de lotes.',
          quotes: [
            {
              source_field: 'content',
              quote: 'Las empresas sancionadas consensuaron ofertas de cobertura y compensaciones económicas cruzadas entre licitadores.'
            }
          ]
        },
        {
          point: 'Activación de la cláusula de prohibición de contratar con el sector público conforme a la Ley de Contratos del Sector Público.',
          quotes: [
            {
              source_field: 'content',
              quote: 'Remítase la presente resolución a la Junta Consultiva de Contratación Pública del Estado para la determinación del alcance y duración de la prohibición de contratar.'
            }
          ]
        }
      ]
    }
  },
  {
    entry_id: '4db3fa9a-280c-4250-b758-97a2c233e1a5',
    title: '[2026] CAT 56 | Sciallis v Fender Musical Instruments Corp Europe',
    source: {
      id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
      name: 'Competition Appeal Tribunal - Judgments',
    },
    published_at: '2026-08-28T11:20:00Z',
    url: 'https://www.catribunal.org.uk/judgments/1589-5-7-23-sciallis-v-fender',
    content_type: 'judgment',
    relevance: {
      status: 'relevant',
      score: 88,
      confidence: 1.0,
    },
    summary: 'Sentencia del CAT en materia de fijación indirecta de precios de reventa (RPM) online. El Tribunal estima parcialmente la reclamación de daños interpuesta por distribuidores independientes de instrumentos musicales afectados por presiones de precios mínimos anunciados.',
    canonical_topics: [
      { code: 'vertical_restraints', name: 'Restricciones verticales' },
      { code: 'damages_actions', name: 'Acciones de daños y perjuicios' },
    ],
    canonical_primary_topic: { code: 'vertical_restraints', name: 'Restricciones verticales' },
    key_points: [
      'Evaluación del daño económico en distribuidores expulsados de plataformas de venta online.',
      'Admisibilidad de modelos estadísticos multivariantes para acreditar el sobreprecio sufrido por los minoristas.',
      'Cómputo del plazo de prescripción a partir de la publicación de la decisión sancionadora de la CMA.'
    ],
    evidence: {
      source: 'deep',
      summary_quotes: [
        {
          source_field: 'content',
          quote: 'The Tribunal finds that the minimum advertised pricing policy operated as a practical restraint on retail price freedom.'
        }
      ],
      key_points: [
        {
          point: 'Evaluación del daño económico en distribuidores expulsados de plataformas de venta online.',
          quotes: [
            {
              source_field: 'content',
              quote: 'Distributors were deprived of competitive margin resulting directly from supplier enforcement mechanisms.'
            }
          ]
        }
      ]
    }
  },
  {
    entry_id: 'c119efb6-ecde-48c6-9a04-490becb6f176',
    title: '[2026] CAT 67 | GLOBAL-365 plc & Another v PayPoint plc & Others (Costs Ruling)',
    source: {
      id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
      name: 'Competition Appeal Tribunal - Judgments',
    },
    published_at: '2026-08-25T15:00:00Z',
    url: 'https://www.catribunal.org.uk/judgments/1597-5-7-23-global-365-plc-v-paypoint-plc',
    content_type: 'judgment',
    relevance: {
      status: 'relevant',
      score: 84,
      confidence: 1.0,
    },
    summary: 'Resolución de costas procesales del CAT tras litigio prolongado por abuso de posición dominante mediante cláusulas de exclusividad en terminales de pago electrónico. Se establecen pautas claras sobre reducción de costas por conductas procesales dilatorias.',
    canonical_topics: [
      { code: 'private_enforcement', name: 'Aplicación privada' },
      { code: 'abuse_dominance', name: 'Abuso de posición de dominio' },
    ],
    canonical_primary_topic: { code: 'private_enforcement', name: 'Aplicación privada' },
    key_points: [
      'Criterios para la ponderación del éxito procesal en reclamaciones híbridas de competencia y contractuales.',
      'Descuento del 20% en las costas recuperables por falta de proporcionalidad en el volumen de prueba pericial.'
    ],
    evidence: {
      source: 'deep',
      summary_quotes: [
        {
          source_field: 'content',
          quote: 'The CAT exercises its discretion under Rule 104 to apportion costs reflecting the mixed outcomes on liability.'
        }
      ],
      key_points: [
        {
          point: 'Criterios para la ponderación del éxito procesal en reclamaciones híbridas de competencia y contractuales.',
          quotes: [
            {
              source_field: 'content',
              quote: 'Where a claimant succeeds on liability but fails significantly on quantum, cost shifting must be nuanced.'
            }
          ]
        }
      ]
    }
  },
  {
    entry_id: '14e036d2-78c9-456b-a633-3796b1710778',
    title: '[2026] EWCA Civ 814 | Rowntree v Performing Right Society Limited',
    source: {
      id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
      name: 'Competition Appeal Tribunal - Judgments',
    },
    published_at: '2026-08-22T09:45:00Z',
    url: 'https://www.catribunal.org.uk/judgments/ewca-civ-814-rowntree-v-prs',
    content_type: 'judgment',
    relevance: {
      status: 'relevant',
      score: 79,
      confidence: 1.0,
    },
    summary: 'Pronunciamiento de la Court of Appeal sobre la legitimación activa de creadores y titulares de derechos frente a entidades de gestión colectiva de derechos de autor por presuntas tarifas discriminatorias y falta de transparencia contable.',
    canonical_topics: [
      { code: 'abuse_dominance', name: 'Abuso de posición de dominio' },
      { code: 'private_enforcement', name: 'Aplicación privada' },
    ],
    canonical_primary_topic: { code: 'abuse_dominance', name: 'Abuso de posición de dominio' },
    key_points: [
      'Límites de la autonomía organizativa de los organismos de gestión colectiva bajo el Derecho antitrust.',
      'Criterios de proporcionalidad en la fijación de tarifas generales frente a operadores independientes de streaming.'
    ],
    evidence: {
      source: 'deep',
      summary_quotes: [
        {
          source_field: 'content',
          quote: 'Collecting societies enjoying a de facto monopoly are subject to strict obligations of objective fairness.'
        }
      ],
      key_points: [
        {
          point: 'Límites de la autonomía organizativa de los organismos de gestión colectiva bajo el Derecho antitrust.',
          quotes: [
            {
              source_field: 'content',
              quote: 'Dominant status requires tariff schedules to reflect demonstrably justifiable administrative costs.'
            }
          ]
        }
      ]
    }
  },
  {
    entry_id: 'f8123abc-5566-4e99-b112-998877665544',
    title: 'Comisión Europea inicia investigación formal a proveedor de cloud por autopreferencia',
    source: {
      id: '7a12b345-c89d-4e5f-b012-3456789abcde',
      name: 'European Commission - DG Competition',
    },
    published_at: '2026-08-19T13:00:00Z',
    url: 'https://ec.europa.eu/competition/antitrust/news/ip_26_8901.html',
    content_type: 'press_release',
    relevance: {
      status: 'uncertain',
      score: 58,
      confidence: 0.75,
    },
    summary: 'Apertura de procedimiento formal antimonopolio para examinar si las prácticas de empaquetamiento comercial y licencias restrictivas de servicios de software empresarial vinculados a infraestructura en la nube restringen la interoperabilidad con competidores.',
    canonical_topics: [
      { code: 'self_preferencing', name: 'Autopreferencia algorítmica' },
      { code: 'digital_markets', name: 'Mercados Digitales y Plataformas' },
    ],
    canonical_primary_topic: { code: 'self_preferencing', name: 'Autopreferencia algorítmica' },
    key_points: [
      'Investigación preliminar sobre el impacto del licenciamiento cruzado en clientes corporativos europeos.',
      'Evaluación de posibles compromisos presentados por el operador para solventar dudas de competencia sin sanción.'
    ],
    evidence: null
  },
  {
    entry_id: 'd9988776-4433-2211-8899-aabbccddeeff',
    title: 'Nota Informativa: Actualización de la Guía de Notificación de Concentraciones 2026',
    source: {
      id: '01b2a969-9524-4f05-b045-8f6fc814f85e',
      name: 'CNMC - Sala de Competencia',
    },
    published_at: '2026-08-14T08:30:00Z',
    url: 'https://www.cnmc.es/guias-concentraciones-2026',
    content_type: 'guide',
    relevance: {
      status: 'uncertain',
      score: 52,
      confidence: 0.8,
    },
    summary: 'Publicación de los nuevos criterios orientativos para el cálculo de cuotas de mercado y umbrales de notificación de concentraciones económicas en sectores intensivos en tecnología y datos.',
    canonical_topics: [
      { code: 'merger_control', name: 'Control de concentraciones' },
    ],
    canonical_primary_topic: { code: 'merger_control', name: 'Control de concentraciones' },
    key_points: [
      'Clarificación del tratamiento de activos intangibles y bases de datos a efectos de volumen de negocio.',
      'Procedimiento abreviado ampliado para operaciones con solapamientos horizontales inferiores al 15%.'
    ],
    evidence: null
  },
  {
    entry_id: 'e1122334-9988-7766-5544-33221100aabb',
    title: 'Designación de miembros del comité asesor de telecomunicaciones',
    source: {
      id: '01b2a969-9524-4f05-b045-8f6fc814f85e',
      name: 'CNMC - Sala de Competencia',
    },
    published_at: '2026-08-10T12:00:00Z',
    url: 'https://www.cnmc.es/acuerdos-personal-2026',
    content_type: 'announcement',
    relevance: {
      status: 'not_relevant',
      score: 18,
      confidence: 0.95,
    },
    summary: 'Acuerdo administrativo de nombramiento y renovación periódica de vocales del comité consultivo sectorial de telecomunicaciones y comunicación audiovisual.',
    canonical_topics: [],
    canonical_primary_topic: null,
    key_points: [
      'Nombramientos de carácter organizativo interno sin trascendencia sobre expedientes sancionadores ni normativa de competencia.'
    ],
    evidence: null
  },
  {
    entry_id: 'a7788990-1122-3344-5566-778899aabbcc',
    title: 'Informe trimestral sobre evolución de tarifas minoristas de banda ancha',
    source: {
      id: '01b2a969-9524-4f05-b045-8f6fc814f85e',
      name: 'CNMC - Sala de Competencia',
    },
    published_at: '2026-08-05T09:15:00Z',
    url: 'https://www.cnmc.es/informes-sectoriales/telecom-q2-2026',
    content_type: 'report',
    relevance: {
      status: 'not_relevant',
      score: 22,
      confidence: 0.9,
    },
    summary: 'Estudio puramente estadístico sobre precios medios y penetración de accesos de fibra óptica en hogares españoles durante el segundo trimestre.',
    canonical_topics: [],
    canonical_primary_topic: null,
    key_points: [
      'Análisis descriptivo de mercado sin imputaciones de conducta anticompetitiva ni valoraciones jurídicas sustantivas.'
    ],
    evidence: null
  }
];

export const MOCK_DASHBOARD: ObservatoryDashboard = {
  total_publications: 80,
  relevant_count: 32,
  uncertain_count: 10,
  not_relevant_count: 38,
  publications_last_7_days: 23,
  publications_last_30_days: 40,
  relevant_last_30_days: 11,
  top_topics: [
    { code: 'collective_actions', name: 'Acciones colectivas y representativas', count: 18 },
    { code: 'damages_actions', name: 'Acciones de daños y perjuicios', count: 16 },
    { code: 'cartels', name: 'Cárteles horizontales', count: 15 },
    { code: 'essential_facilities', name: 'Instalaciones esenciales y denegación de suministro', count: 12 },
    { code: 'vertical_restraints', name: 'Restricciones verticales', count: 9 },
  ],
  top_sources: [
    {
      source_id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
      name: 'Competition Appeal Tribunal - Judgments',
      publication_count: 20,
      relevant_count: 19,
    },
    {
      source_id: 'e6f47df4-3d9a-412e-9d2a-89a03fc5b931',
      name: 'Court of Justice of the European Union (CJEU)',
      publication_count: 20,
      relevant_count: 14,
    },
    {
      source_id: '01b2a969-9524-4f05-b045-8f6fc814f85e',
      name: 'CNMC - Sala de Competencia',
      publication_count: 20,
      relevant_count: 8,
    },
    {
      source_id: '7a12b345-c89d-4e5f-b012-3456789abcde',
      name: 'European Commission - DG Competition',
      publication_count: 20,
      relevant_count: 6,
    },
  ],
  latest_relevant_entries: [
    {
      entry_id: 'ada5d125-a861-4ff8-bcf6-2b2607afd834',
      title: '[2026] EWCA Civ 993 | Dr Liza Lovdahl Gormsen v Meta Platforms, Inc.',
      source: {
        id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
        name: 'Competition Appeal Tribunal - Judgments',
      },
      published_at: '2026-09-03T14:30:00Z',
      score: 96,
      summary: 'La Court of Appeal desestima el recurso de apelación de Meta Platforms en el marco de la acción colectiva opt-out sobre condiciones abusivas de explotación de datos de usuarios de Facebook.',
      canonical_topics: [
        { code: 'collective_actions', name: 'Acciones colectivas y representativas' },
      ],
    },
    {
      entry_id: '27c1a107-ebfe-40d0-ba9e-d7d399c3c565',
      title: 'Asunto C-60/25 [Livronsa] | Sentencia del Tribunal de Justicia (Gran Sala)',
      source: {
        id: 'e6f47df4-3d9a-412e-9d2a-89a03fc5b931',
        name: 'Court of Justice of the European Union (CJEU)',
      },
      published_at: '2026-09-03T09:00:00Z',
      score: 95,
      summary: 'Sentencia capital sobre abuso de posición de dominio en mercados farmacéuticos. El TJUE declara que la negativa injustificada de un laboratorio dominante a suministrar fármacos constituye infracción del artículo 102 TFUE.',
      canonical_topics: [
        { code: 'essential_facilities', name: 'Instalaciones esenciales y denegación de suministro' },
      ],
    },
    {
      entry_id: 'b140263f-9173-4554-94e4-b7c10b05bfe1',
      title: 'Resolución S/0014/24: Cártel de licitaciones de infraestructuras ferroviarias',
      source: {
        id: '01b2a969-9524-4f05-b045-8f6fc814f85e',
        name: 'CNMC - Sala de Competencia',
      },
      published_at: '2026-09-02T10:15:00Z',
      score: 92,
      summary: 'La CNMC sanciona con 47 millones de euros a 6 empresas constructoras por coordinar ofertas y repartirse licitaciones públicas de electrificación ferroviaria durante 8 años.',
      canonical_topics: [
        { code: 'cartels', name: 'Cárteles horizontales' },
      ],
    },
    {
      entry_id: '4db3fa9a-280c-4250-b758-97a2c233e1a5',
      title: '[2026] CAT 56 | Sciallis v Fender Musical Instruments Corp Europe',
      source: {
        id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
        name: 'Competition Appeal Tribunal - Judgments',
      },
      published_at: '2026-08-28T11:20:00Z',
      score: 88,
      summary: 'Sentencia del CAT en materia de fijación indirecta de precios de reventa (RPM) online. El Tribunal estima parcialmente la reclamación de daños interpuesta por distribuidores independientes.',
      canonical_topics: [
        { code: 'vertical_restraints', name: 'Restricciones verticales' },
      ],
    },
    {
      entry_id: 'c119efb6-ecde-48c6-9a04-490becb6f176',
      title: '[2026] CAT 67 | GLOBAL-365 plc & Another v PayPoint plc & Others (Costs Ruling)',
      source: {
        id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
        name: 'Competition Appeal Tribunal - Judgments',
      },
      published_at: '2026-08-25T15:00:00Z',
      score: 84,
      summary: 'Resolución de costas procesales del CAT tras litigio prolongado por abuso de posición dominante mediante cláusulas de exclusividad en terminales de pago electrónico.',
      canonical_topics: [
        { code: 'private_enforcement', name: 'Aplicación privada' },
      ],
    },
  ],
};

function getAllTopicCodes(rootCode: string, nodes: ObservatoryTopicNode[]): string[] {
  const codes: string[] = [];
  function traverse(node: ObservatoryTopicNode) {
    if (node.code === rootCode || codes.includes(node.parent_code || '')) {
      codes.push(node.code);
    }
    for (const child of node.children) {
      if (node.code === rootCode || codes.includes(node.code)) {
        codes.push(child.code);
      }
      traverse(child);
    }
  }
  for (const root of nodes) {
    if (root.code === rootCode) {
      codes.push(root.code);
      const collect = (n: ObservatoryTopicNode) => {
        for (const c of n.children) {
          codes.push(c.code);
          collect(c);
        }
      };
      collect(root);
      return codes;
    }
    traverse(root);
  }
  return codes.length > 0 ? codes : [rootCode];
}

export function filterMockEntries(params: EntriesQueryParams): ObservatoryListResponse {
  let list = [...MOCK_ENTRIES];

  // 1. Text search q
  if (params.q) {
    const qLower = params.q.toLowerCase().trim();
    list = list.filter(item => {
      const inTitle = item.title.toLowerCase().includes(qLower);
      const inSummary = item.summary.toLowerCase().includes(qLower);
      const inKeyPoints = item.key_points.some(kp => kp.toLowerCase().includes(qLower));
      return inTitle || inSummary || inKeyPoints;
    });
  }

  // 2. Relevance Status
  if (params.relevance_status) {
    list = list.filter(item => item.relevance.status === params.relevance_status);
  }

  // 3. Min Relevance Score
  if (params.min_relevance_score !== undefined && params.min_relevance_score !== null) {
    list = list.filter(item => item.relevance.score >= (params.min_relevance_score ?? 0));
  }

  // 4. Source filter
  if (params.source_id) {
    list = list.filter(item => item.source.id === params.source_id);
  }
  if (params.source_ids && params.source_ids.length > 0) {
    list = list.filter(item => params.source_ids!.includes(item.source.id));
  }

  // 5. Topic filter (hierarchical expansion)
  if (params.topic_code) {
    const validCodes = getAllTopicCodes(params.topic_code, MOCK_TOPICS);
    list = list.filter(item =>
      item.canonical_topics.some(t => validCodes.includes(t.code))
    );
  }

  // 6. Dates
  if (params.date_from) {
    const fromDate = new Date(params.date_from);
    list = list.filter(item => new Date(item.published_at) >= fromDate);
  }
  if (params.date_to) {
    const toDate = new Date(params.date_to);
    // End of day
    toDate.setHours(23, 59, 59, 999);
    list = list.filter(item => new Date(item.published_at) <= toDate);
  }

  // 7. Sort
  const sortBy = params.sort_by || 'published_at';
  const sortOrder = params.sort_order || 'desc';
  list.sort((a, b) => {
    let comp = 0;
    if (sortBy === 'relevance_score') {
      comp = a.relevance.score - b.relevance.score;
    } else {
      comp = new Date(a.published_at).getTime() - new Date(b.published_at).getTime();
    }
    return sortOrder === 'asc' ? comp : -comp;
  });

  const total = list.length;
  const limit = params.limit || 20;
  const offset = params.offset || 0;
  const paged = list.slice(offset, offset + limit).map(item => {
    // Strip evidence from list item as per contract
    const { evidence: _evidence, ...listItem } = item;
    return listItem as ObservatoryEntryListItem;
  });

  return {
    items: paged,
    total,
    limit,
    offset,
  };
}

export function getMockEntryDetail(entryId: string): ObservatoryEntryDetail | null {
  const found = MOCK_ENTRIES.find(e => e.entry_id === entryId);
  return found || null;
}
