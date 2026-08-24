const { render } = require('../lib/activity-log.js');

describe('ActivityLog', () => {
  let mockDoc;
  let mockElement;

  beforeEach(() => {
    mockElement = { 
      innerHTML: '', 
      querySelector: () => mockElement, 
      createElement: (tag) => ({ 
        className: '', 
        textContent: '', 
        appendChild: () => {}, 
        style: {} 
      }) 
    };
    mockDoc = { 
      querySelector: () => mockElement, 
      createElement: mockElement.createElement 
    };
    global.document = mockDoc;
  });

  test('renders activity turns', () => {
    render({ turns: [{ ts: '2024-01-01T00:00:00', content: { text: 'Test activity' } }] });
    expect(mockElement.innerHTML).toContain('Test activity');
  });

  test('renders empty state when no turns', () => {
    render({ turns: [] });
    expect(mockElement.innerHTML).toContain('No activity recorded yet.');
  });
});
