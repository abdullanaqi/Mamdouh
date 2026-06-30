/** Domain-level errors with stable codes for the UI/server-action layer. */
export class DomainError extends Error {
  constructor(
    message: string,
    public code: 'not_found' | 'forbidden' | 'validation' | 'conflict' = 'validation',
  ) {
    super(message);
    this.name = 'DomainError';
  }
}

export function notFound(what: string): never {
  throw new DomainError(`${what} not found`, 'not_found');
}

export function forbidden(message = 'Not allowed'): never {
  throw new DomainError(message, 'forbidden');
}
