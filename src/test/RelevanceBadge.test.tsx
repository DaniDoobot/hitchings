import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { RelevanceBadge } from '../components/common/RelevanceBadge';

describe('RelevanceBadge Component', () => {
  it('renders "Relevante" label for status relevant', () => {
    render(<RelevanceBadge status="relevant" score={95} />);
    expect(screen.getByText('Relevante')).toBeInTheDocument();
    expect(screen.getByText('95/100')).toBeInTheDocument();
  });

  it('renders "Incierto" label for status uncertain', () => {
    render(<RelevanceBadge status="uncertain" score={50} />);
    expect(screen.getByText('Incierto')).toBeInTheDocument();
    expect(screen.getByText('50/100')).toBeInTheDocument();
  });

  it('renders "No relevante" label for status not_relevant', () => {
    render(<RelevanceBadge status="not_relevant" score={20} />);
    expect(screen.getByText('No relevante')).toBeInTheDocument();
    expect(screen.getByText('20/100')).toBeInTheDocument();
  });

  it('does NOT recompute semantic status based on score (status is authoritative)', () => {
    // High score but status explicitly not_relevant
    render(<RelevanceBadge status="not_relevant" score={99} />);
    expect(screen.getByText('No relevante')).toBeInTheDocument();
    expect(screen.queryByText('Relevante')).not.toBeInTheDocument();
  });

  it('hides score when showScore is false', () => {
    render(<RelevanceBadge status="relevant" score={85} showScore={false} />);
    expect(screen.getByText('Relevante')).toBeInTheDocument();
    expect(screen.queryByText('85')).not.toBeInTheDocument();
  });
});
