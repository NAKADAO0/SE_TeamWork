export function createStore(initialState, reduce) {
  let state = initialState;
  const subscribers = new Set();
  return {
    getState() {
      return state;
    },
    dispatch(action) {
      const next = reduce(state, action);
      if (next === state) return state;
      state = next;
      for (const subscriber of subscribers) subscriber(state);
      return state;
    },
    subscribe(subscriber) {
      subscribers.add(subscriber);
      return () => subscribers.delete(subscriber);
    },
  };
}
